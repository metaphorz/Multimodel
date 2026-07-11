# -*- coding: utf-8 -*-
"""
General hurricane PDE (marine) isotach plotter — MPS‑safe
========================================================
- Apple Silicon friendly (MPS backend). CPU fallback safe.
- Steady slab BL PDE (marine); Holland pressure; Large & Pond drag.
- Big, labeled isotachs in knots (threshold configurable).
- Includes storm name & date in titles and filenames so multiple storms can be run.

Example (Hurricane Charley, landfall vicinity):
  /usr/local/bin/python3 hurricane_pde_marine.py \
      --storm-name "Charley" --storm-date 2004-08-13 \
      --lat0 26.6 --lon0 -82.2 \
      --speed-mph 21 --bearing-deg 25 \
      --B 1.7 --rmax-core-km 10 --dp-hpa 72 \
      --out out_charley --core-window-km 100

Outputs:
  out_charley/charley-2004-08-13_pde_marine_full_kt.png
  out_charley/charley-2004-08-13_pde_marine_core_kt.png
"""
import math, argparse, re
from pathlib import Path
import numpy as np
import torch
import matplotlib.pyplot as plt

torch.set_float32_matmul_precision('high')

# -------------------------
# Utilities & numerics
# -------------------------
def device_select():
    if torch.backends.mps.is_available():
        return torch.device('mps')
    return torch.device('cpu')

def roll(F, shift, dim): return torch.roll(F, shifts=shift, dims=dim)
def ddphi(F, dphi): return (roll(F, -1, 1) - roll(F, 1, 1)) / (2.0*dphi)
def d2phi(F, dphi): return (roll(F, -1, 1) - 2.0*F + roll(F, 1, 1)) / (dphi**2)
def ddr(F, dr):
    G = torch.zeros_like(F)
    G[1:-1,:] = (F[2:,:] - F[:-2,:])/(2.0*dr)
    G[0,:]    = (F[1,:]  - F[0,:]) / dr
    G[-1,:]   = (F[-1,:] - F[-2,:]) / dr
    return G
def d2r(F, dr):
    G = torch.zeros_like(F)
    G[1:-1,:] = (F[2:,:] - 2.0*F[1:-1,:] + F[:-2,:])/(dr**2)
    G[0,:]    = (F[1,:] - 2.0*F[0,:] + F[0,:])/(dr**2)
    G[-1,:]   = (F[-1,:] - 2.0*F[-2,:] + F[-3,:])/(dr**2)
    return G
def laplacian(F, rr, dr, dphi):
    Fr = ddr(F, dr)
    return d2r(F, dr) + (1.0/rr)*Fr + (1.0/(rr**2))*d2phi(F, dphi)

def inflow_angle_rad(rr, Rmax_m):
    s = rr / Rmax_m
    tmax, tfar, tin = 25.0, 8.0, 15.0
    bump = tmax * torch.exp(-((s-1.0)**2)/0.4)
    outward = tfar * (1.0 - torch.exp(-torch.clamp(s-1.0,min=0.0)**2/1.2))
    inward  = tin  * (1.0 - torch.exp(-torch.clamp(1.0-s,min=0.0)**2/0.2))
    return (bump + outward + inward) * (math.pi/180.0)

def Cd_LP(U10):  # Large & Pond neutral drag
    return torch.clamp(1e-3*(0.63 + 0.066*U10), 0.8e-3, 3.5e-3)

def Cd_from_z0(z0):
    """Calculate drag coefficient from surface roughness using log law"""
    kappa, zref = 0.4, 10.0
    z0c = torch.clamp(z0, 1e-5, zref/10.0)
    Cd = (kappa / torch.log(torch.tensor(zref, device=z0.device) / z0c))**2
    return torch.clamp(Cd, 0.8e-3, 5.0e-3)

def load_z0_map(path):
    """Load Florida roughness map and convert to z0 values"""
    from PIL import Image
    img = Image.open(path).convert('RGB')
    W, H = img.size
    rgb = torch.from_numpy(np.asarray(img).astype(np.float32)/255.0)
    
    # Convert to grayscale
    gray = 0.299*rgb[...,0] + 0.587*rgb[...,1] + 0.114*rgb[...,2]
    
    # Map grayscale to roughness values
    z0_min, z0_max = 2e-4, 1.5
    z0 = z0_min * (z0_max / z0_min) ** torch.clamp(gray, 0.0, 1.0)
    
    return z0.to(dtype=torch.float32), W, H

def bilinear_sample_z0(z0, W, H, lat, lon, lat_min, lat_max, lon_min, lon_max):
    """Bilinear interpolation of z0 values at given lat/lon"""
    col = (lon - lon_min) / (lon_max - lon_min) * (W - 1)
    row = (lat_max - lat) / (lat_max - lat_min) * (H - 1)
    
    c0 = torch.floor(col).to(torch.int64)
    c1 = torch.clamp(c0+1, 0, W-1)
    r0 = torch.floor(row).to(torch.int64)
    r1 = torch.clamp(r0+1, 0, H-1)
    
    wc = col - c0
    wr = row - r0
    
    z00 = z0[torch.clamp(r0,0,H-1), torch.clamp(c0,0,W-1)]
    z01 = z0[torch.clamp(r0,0,H-1), torch.clamp(c1,0,W-1)]
    z10 = z0[torch.clamp(r1,0,H-1), torch.clamp(c0,0,W-1)]
    z11 = z0[torch.clamp(r1,0,H-1), torch.clamp(c1,0,W-1)]
    
    return (1-wr)*((1-wc)*z00 + wc*z01) + wr*((1-wc)*z10 + wc*z11)

def apply_roughness_effects(spd_ms, r, phi, args, device, windfield_model='pde'):
    """Apply surface roughness effects to windfield if z0 image is provided"""
    if args.z0_img is None or not hasattr(args, 'lat0') or not hasattr(args, 'lon0'):
        return spd_ms
    
    try:
        # Load z0 map
        z0_cpu, W, H = load_z0_map(args.z0_img)
        z0 = z0_cpu.to(device)
        
        # Create meshgrid for polar coordinates
        R, Phi = torch.meshgrid(r, phi, indexing='ij')
        
        # Convert to Cartesian km coordinates relative to storm center
        deg2km_lat = 110.574
        deg2km_lon = 111.320 * math.cos(math.radians(args.lat0))
        
        X_km = (R / 1000.0) * torch.cos(Phi)  # East-west
        Y_km = (R / 1000.0) * torch.sin(Phi)  # North-south
        
        # Convert to lat/lon
        lat = torch.tensor(args.lat0, device=device) + Y_km / deg2km_lat
        lon = torch.tensor(args.lon0, device=device) + X_km / deg2km_lon
        
        # Sample z0 at all polar grid points
        z0_field = bilinear_sample_z0(
            z0, W, H, lat, lon,
            torch.tensor(args.lat_min, device=device),
            torch.tensor(args.lat_max, device=device),
            torch.tensor(args.lon_min, device=device),
            torch.tensor(args.lon_max, device=device)
        )
        
        # Apply light smoothing to z0 field to reduce artifacts
        if z0_field.ndim == 2:
            kernel = torch.tensor([[1, 2, 1], [2, 4, 2], [1, 2, 1]], device=device, dtype=torch.float32) / 16.0
            z0_padded = torch.nn.functional.pad(z0_field.unsqueeze(0).unsqueeze(0), (1, 1, 1, 1), mode='reflect')
            z0_smooth = torch.nn.functional.conv2d(z0_padded, kernel.unsqueeze(0).unsqueeze(0), padding=0)
            z0_field = z0_smooth.squeeze()
        
        # Apply roughness effects only within specified zone
        distance_from_center = R / 1000.0  # Convert to km
        roughness_zone = distance_from_center <= args.roughness_zone_km
        
        water_mask = z0_field <= args.water_z0_threshold
        
        if windfield_model == 'willoughby':
            # For Willoughby, use very gentle uniform reduction to preserve structure
            roughness_factor = torch.where(
                (~roughness_zone) | water_mask,
                torch.tensor(1.0, device=device),
                torch.tensor(0.95, device=device)
            )
        else:
            # For PDE and Holland, use log-based roughness factor
            log_z0 = torch.log10(torch.clamp(z0_field, 1e-5, 2.0))
            log_z0_norm = (log_z0 + 5.0) / 5.3
            
            roughness_factor = torch.where(
                (~roughness_zone) | water_mask,
                torch.tensor(1.0, device=device),
                torch.clamp(0.80 + 0.20 * (1.0 - log_z0_norm), 0.80, 1.0)
            )
        
        return spd_ms * roughness_factor
        
    except Exception as e:
        print(f"[WARNING] Failed to apply roughness effects: {e}")
        return spd_ms

def build_radial_grid(rmin_km, rmax_km, Nr, gamma=2.5, device=None):
    rmin = rmin_km*1000.0; rmax = rmax_km*1000.0
    q = torch.linspace(0.0, 1.0, Nr, device=device)
    r = rmin + (rmax - rmin) * (q ** gamma)
    return r

def slugify(text):
    s = text.strip().lower()
    s = re.sub(r'[^a-z0-9]+', '-', s)
    s = re.sub(r'-+', '-', s).strip('-')
    return s

# -------------------------
# Robust polar interpolation (MPS-safe)
# -------------------------
def _index2d_linear(F, r_idx, p_idx):
    Nr, Np = F.shape
    flat = F.reshape(-1)
    lin = (r_idx * Np + p_idx).reshape(-1)
    out = flat[lin]
    return out.reshape(r_idx.shape)

def bilinear_polar(F, r_src, phi_src, r_tgt, phi_tgt):
    device = F.device
    Nr, Np = F.shape
    if r_tgt.ndim == 1 and phi_tgt.ndim == 1:
        r_tgt, phi_tgt = torch.meshgrid(r_tgt, phi_tgt, indexing='ij')
    elif r_tgt.ndim != 2 or phi_tgt.ndim != 2 or r_tgt.shape != phi_tgt.shape:
        raise ValueError("r_tgt and phi_tgt must be 1D each or 2D with the same shape")
    # CPU searchsorted for nonuniform radials
    r_src_cpu = r_src.detach().to('cpu')
    r_tgt_cpu = r_tgt.detach().to('cpu')
    r_idx1_cpu = torch.searchsorted(r_src_cpu, r_tgt_cpu)
    r_idx0_cpu = torch.clamp(r_idx1_cpu - 1, 0, Nr-2)
    r_idx1_cpu = torch.clamp(r_idx1_cpu, 1, Nr-1)
    r0 = r_idx0_cpu.to(device=device, dtype=torch.int64)
    r1 = r_idx1_cpu.to(device=device, dtype=torch.int64)
    r0v = r_src[r0]; r1v = r_src[r1]
    denom = (r1v - r0v)
    denom = torch.where(denom == 0, torch.ones_like(denom), denom)
    wr = (r_tgt - r0v) / denom
    # uniform azimuthal
    dphi = phi_src[1]-phi_src[0]
    tp = (phi_tgt % (2*math.pi)) / dphi
    p0 = torch.floor(tp).to(torch.int64) % Np
    p1 = (p0 + 1) % Np
    wp = tp - torch.floor(tp)
    # gather
    F_r0p0 = _index2d_linear(F, r0, p0)
    F_r1p0 = _index2d_linear(F, r1, p0)
    F_r0p1 = _index2d_linear(F, r0, p1)
    F_r1p1 = _index2d_linear(F, r1, p1)
    return (1-wr)*(1-wp)*F_r0p0 + wr*(1-wp)*F_r1p0 + (1-wr)*wp*F_r0p1 + wr*wp*F_r1p1

# -------------------------
# Physics terms
# -------------------------
def physics_terms(u, v, rr, dphi, dr, f, dpdr, rho, Kh, beta10, h_bl):
    ur = ddr(u, dr); uphi = ddphi(u, dphi)
    vr = ddr(v, dr); vphi = ddphi(v, dphi)
    adv_u = u*ur + (v/rr)*uphi - (v**2)/rr
    adv_v = u*vr + (v/rr)*vphi + (u*v)/rr
    lap_u = laplacian(u, rr, dr, dphi)
    lap_v = laplacian(v, rr, dr, dphi)
    U_mag = torch.sqrt(u**2 + v**2) + 1e-6
    U10_mag = beta10 * U_mag
    Cd = Cd_LP(U10_mag)
    drag_coeff = (Cd * U10_mag / h_bl) * beta10
    rhs_u = -(adv_u) - f*v - (dpdr / rho) + Kh*lap_u - drag_coeff*u
    rhs_v = -(adv_v) + f*u           + Kh*lap_v - drag_coeff*v
    return rhs_u, rhs_v, U_mag

# Batch-safe derivative helpers for the dynamic solver: identical arithmetic to
# ddphi/d2phi/ddr/d2r/laplacian on a 2-D (Nr,Nphi) field, but written with
# ellipsis indexing / negative dims so a batched (B,Nr,Nphi) field works too.
def ddphi_b(F, dphi): return (roll(F, -1, -1) - roll(F, 1, -1)) / (2.0*dphi)
def d2phi_b(F, dphi): return (roll(F, -1, -1) - 2.0*F + roll(F, 1, -1)) / (dphi**2)
def ddr_b(F, dr):
    G = torch.zeros_like(F)
    G[..., 1:-1, :] = (F[..., 2:, :] - F[..., :-2, :])/(2.0*dr)
    G[..., 0, :]    = (F[..., 1, :]  - F[..., 0, :]) / dr
    G[..., -1, :]   = (F[..., -1, :] - F[..., -2, :]) / dr
    return G
def d2r_b(F, dr):
    G = torch.zeros_like(F)
    G[..., 1:-1, :] = (F[..., 2:, :] - 2.0*F[..., 1:-1, :] + F[..., :-2, :])/(dr**2)
    G[..., 0, :]    = (F[..., 1, :] - F[..., 0, :])/(dr**2)
    G[..., -1, :]   = (F[..., -1, :] - 2.0*F[..., -2, :] + F[..., -3, :])/(dr**2)
    return G
def laplacian_b(F, rr, dr, dphi):
    return d2r_b(F, dr) + (1.0/rr)*ddr_b(F, dr) + (1.0/(rr**2))*d2phi_b(F, dphi)

def ddr_upwind(F, dr, w):
    """First-order upwind radial derivative (sign of the advecting radial wind w);
    edge values replicated. Shock capture for the slab-BL inflow shock."""
    Fp = torch.cat([F[..., 1:, :], F[..., -1:, :]], dim=-2)
    Fm = torch.cat([F[..., :1, :], F[..., :-1, :]], dim=-2)
    return torch.where(w > 0, F - Fm, Fp - F) / dr

def physics_terms_dyn(u, v, rr, dphi, dr, f, dpdr, rho, Kh, beta10, h_bl, z0_field=None):
    """Tendencies for the DYNAMIC (long physical-time) integration. Two deliberate
    differences from physics_terms, each required for multi-hour stability
    (physics_terms is only ever marched ~48 s of pseudo-time by the steady solver):
      1. Coriolis signs corrected to the cylindrical-coordinate momentum equations
         (+f*v radial, -f*u tangential). The flipped signs make a cyclone
         inertially unstable (measured e-fold ~2e3 s); effect on the short steady
         solve is < 0.2 mph.
      2. RADIAL advection is first-order upwind: the slab TC boundary layer forms
         an inflow shock near the core (Smith & Vogl 2008) and centered
         differences blow up on it (measured at ~8e3 s). Azimuthal advection
         stays centered (smooth, periodic).
    z0_field (m): surface under each node; land (z0 > 1e-3 m) -> log-law drag,
    water keeps the wind-speed-dependent Large & Pond marine drag."""
    ur = ddr_upwind(u, dr, u); uphi = ddphi_b(u, dphi)
    vr = ddr_upwind(v, dr, u); vphi = ddphi_b(v, dphi)
    adv_u = u*ur + (v/rr)*uphi - (v**2)/rr
    adv_v = u*vr + (v/rr)*vphi + (u*v)/rr
    lap_u = laplacian_b(u, rr, dr, dphi)
    lap_v = laplacian_b(v, rr, dr, dphi)
    U_mag = torch.sqrt(u**2 + v**2) + 1e-6
    U10_mag = beta10 * U_mag
    if z0_field is None:
        Cd = Cd_LP(U10_mag)
    else:
        Cd = torch.where(z0_field > 1e-3, Cd_from_z0(z0_field), Cd_LP(U10_mag))
    drag_coeff = (Cd * U10_mag / h_bl) * beta10
    rhs_u = -(adv_u) + f*v - (dpdr / rho) + Kh*lap_u - drag_coeff*u
    rhs_v = -(adv_v) - f*u            + Kh*lap_v - drag_coeff*v
    return rhs_u, rhs_v, U_mag

# -------------------------
# PDE steady solver (marine only)
# -------------------------
def pde_steady_marine(args, device=None):
    if device is None: device = device_select()
    deg2rad = math.pi/180.0
    phi0 = args.lat0 * deg2rad
    Omega = 7.2921159e-5
    f = torch.tensor(2*Omega*math.sin(phi0), device=device, dtype=torch.float32)
    rho = torch.tensor(1.15, device=device, dtype=torch.float32)
    dp_Pa = torch.tensor(args.dp_hpa * 100.0, device=device, dtype=torch.float32)
    B = torch.tensor(args.B, device=device, dtype=torch.float32)
    Rmax_m = torch.tensor(args.rmax_core_km*1000.0, device=device, dtype=torch.float32)
    beta10 = torch.tensor(args.beta10, device=device, dtype=torch.float32)
    h_bl = torch.tensor(args.h_bl, device=device, dtype=torch.float32)
    # Translation (mph->m/s; 0=N, 90=E)
    mph_to_ms = 0.44704
    c_ms = torch.tensor(args.speed_mph * mph_to_ms, device=device, dtype=torch.float32)
    theta = torch.tensor(args.bearing_deg * deg2rad, device=device, dtype=torch.float32)
    c_x = c_ms * torch.sin(theta); c_y = c_ms * torch.cos(theta)
    # Grid
    r = build_radial_grid(args.rmin_km, args.rmax_km, args.Nr, gamma=args.stretch_gamma, device=device)
    phi_g = torch.linspace(0.0, 2*math.pi, args.Nphi+1, device=device, dtype=torch.float32)[:-1]
    dr = torch.mean(r[1:] - r[:-1])
    dphi = phi_g[1]-phi_g[0]
    rr, pp = torch.meshgrid(r, phi_g, indexing='ij')
    erx = torch.cos(pp); ery = torch.sin(pp)
    etx = -torch.sin(pp); ety =  torch.cos(pp)
    # Holland gradient wind
    exp_term = torch.exp(- (Rmax_m / rr)**B)
    dpdr = dp_Pa * exp_term * (B * (Rmax_m**B) * (rr**(-B-1)))
    fr = f * rr
    Vg = 0.5*(-fr + torch.sqrt(fr**2 + 4.0*rr*(dpdr/rho)))
    # Initial u,v with inflow angle
    theta_in = inflow_angle_rad(rr, Rmax_m)
    V10 = beta10 * Vg
    u = - V10 * torch.sin(theta_in)
    v =   V10 * torch.cos(theta_in)
    # Piecewise Kh
    Kh_inner = torch.tensor(args.Kh_inner, device=device, dtype=torch.float32)
    Kh_outer = torch.tensor(args.Kh_outer, device=device, dtype=torch.float32)
    Kh_field = torch.where(rr <= (1.5*Rmax_m), Kh_inner, Kh_outer)
    # March to steady
    for n in range(args.iter):
        rhs_u, rhs_v, U_mag = physics_terms(u, v, rr, dphi, dr, f, dpdr, rho, Kh_field, beta10, h_bl)
        max_U = torch.max(U_mag); min_r = torch.min(rr)
        dx_min = torch.minimum(dr, min_r * dphi)
        dt_adv = args.cfl * dx_min / (max_U + 1e-3)
        dt_diff = 0.22 * (dx_min**2) / (torch.max(Kh_field) + 1.0)
        dt = torch.minimum(torch.minimum(dt_adv, dt_diff), torch.tensor(3.0, device=device))
        u = u + dt*rhs_u; v = v + dt*rhs_v
        # boundaries
        u[0,:] = 0.0; v[0,:] = 0.0
        Vg_outer = Vg[-1,:]
        v[-1,:] = 0.95*v[-1,:] + 0.05*(beta10*Vg_outer)
        u[-1,:] = 0.9*u[-1,:]
    # Earth-relative speed
    Ux = u * erx + v * etx
    Uy = u * ery + v * ety
    Ux_er = Ux + c_x; Uy_er = Uy + c_y
    speed_ms = torch.sqrt(Ux_er**2 + Uy_er**2)
    # Apply surface roughness effects if provided
    speed_ms = apply_roughness_effects(speed_ms, r, phi_g, args, device, 'pde')
    
    meta = dict(r=r, phi=phi_g, Rmax=args.rmax_core_km, base_tag="pde_marine", model_label="PDE (marine)")
    return speed_ms, meta

# -------------------------
# PDE dynamic solver: physical-time march with evolving forcing
# -------------------------
def pde_dynamic_marine(args, dyn, device=None):
    """Integrate the boundary-layer momentum equations onward in physical time
    while the forcing evolves (storm crossing the coast).

    Grid, initial condition and boundary treatment match pde_steady_marine, but
    tendencies come from physics_terms_dyn (corrected Coriolis signs + upwind
    radial advection -- both REQUIRED for multi-hour stability; see its
    docstring), and the spin-up runs to true convergence instead of a fixed 800
    iterations. Beyond that: (a) the march continues in physical time, (b) the
    pressure-gradient forcing may be rescaled per step (decay applied through
    the PRESSURE, so the wind responds with its physical lag), and (c) the drag
    may come from the land roughness under each node (asymmetric land drag)
    instead of marine Large & Pond everywhere. Drag acts on the storm-relative
    wind and the translation vector is added at output time, exactly as in the
    steady solver.

    dyn (namespace) fields:
      t0_h, t1_h     physical window in hours (t=0: storm center at ew=0)
      dt_forcing_s   forcing-update / sampling interval in seconds (e.g. 60)
      dp_scale       callable t_h -> multiplier on the pressure-gradient term,
                     or None (constant marine forcing)
      z0_fn          callable (ew_mi, ns_mi tensors) -> z0 (m) at those earth
                     positions, or None (marine drag everywhere)
      sample_ew, sample_ns  (N,) tensors, earth-frame sample points in miles
                     (+ew west of the t=0 center, +ns north)
      spinup_iter    steady spin-up iterations at t0 forcing (e.g. 800)
      snap_times_h   optional list of times to keep full earth-relative speed
                     snapshots (nearest forcing step)

    Returns (series, times_h, snaps, meta):
      series (N, nt) earth-relative wind speed (m/s) at the sample points,
      times_h (nt,), snaps {t_h: (Nr,Nphi) speed}, meta dict(r, phi, ...).
    """
    if device is None: device = device_select()
    deg2rad = math.pi/180.0
    MILE_M = 1609.344
    phi0 = args.lat0 * deg2rad
    Omega = 7.2921159e-5
    f = torch.tensor(2*Omega*math.sin(phi0), device=device, dtype=torch.float32)
    rho = torch.tensor(1.15, device=device, dtype=torch.float32)
    dp_Pa = torch.tensor(args.dp_hpa * 100.0, device=device, dtype=torch.float32)
    B = torch.tensor(args.B, device=device, dtype=torch.float32)
    Rmax_m = torch.tensor(args.rmax_core_km*1000.0, device=device, dtype=torch.float32)
    beta10 = torch.tensor(args.beta10, device=device, dtype=torch.float32)
    h_bl = torch.tensor(args.h_bl, device=device, dtype=torch.float32)
    mph_to_ms = 0.44704
    c_ms = torch.tensor(args.speed_mph * mph_to_ms, device=device, dtype=torch.float32)
    theta = torch.tensor(args.bearing_deg * deg2rad, device=device, dtype=torch.float32)
    c_x = c_ms * torch.sin(theta); c_y = c_ms * torch.cos(theta)
    r = build_radial_grid(args.rmin_km, args.rmax_km, args.Nr, gamma=args.stretch_gamma, device=device)
    phi_g = torch.linspace(0.0, 2*math.pi, args.Nphi+1, device=device, dtype=torch.float32)[:-1]
    dr = torch.mean(r[1:] - r[:-1])
    dphi = phi_g[1]-phi_g[0]
    rr, pp = torch.meshgrid(r, phi_g, indexing='ij')
    erx = torch.cos(pp); ery = torch.sin(pp)
    etx = -torch.sin(pp); ety =  torch.cos(pp)
    exp_term = torch.exp(- (Rmax_m / rr)**B)
    dpdr0 = dp_Pa * exp_term * (B * (Rmax_m**B) * (rr**(-B-1)))
    fr = f * rr
    theta_in = inflow_angle_rad(rr, Rmax_m)
    Kh_inner = torch.tensor(args.Kh_inner, device=device, dtype=torch.float32)
    Kh_outer = torch.tensor(args.Kh_outer, device=device, dtype=torch.float32)
    Kh_field = torch.where(rr <= (1.5*Rmax_m), Kh_inner, Kh_outer)
    dt_cap = torch.tensor(3.0, device=device)
    rmax_out = r[-1]

    # earth-frame position (miles) under each polar node when the center is at ewc
    x_e_mi = (rr * erx) / MILE_M          # +east of center
    y_n_mi = (rr * ery) / MILE_M          # +north of center

    def forcing_at(t_h):
        sdp = float(dyn.dp_scale(t_h)) if dyn.dp_scale is not None else 1.0
        dpdr_t = dpdr0 * sdp
        Vg_t = 0.5*(-fr + torch.sqrt(fr**2 + 4.0*rr*(dpdr_t/rho)))
        z0_field = None
        if dyn.z0_fn is not None:
            ewc = args.speed_mph * t_h                    # miles west of landfall
            z0_field = dyn.z0_fn(ewc - x_e_mi, y_n_mi)
        return dpdr_t, Vg_t, z0_field

    def cfl_step(u, v, dpdr_t, Vg_t, z0_field, budget_s):
        rhs_u, rhs_v, U_mag = physics_terms_dyn(u, v, rr, dphi, dr, f, dpdr_t, rho,
                                                Kh_field, beta10, h_bl, z0_field)
        max_U = torch.max(U_mag); min_r = torch.min(rr)
        dx_min = torch.minimum(dr, min_r * dphi)
        dt_adv = args.cfl * dx_min / (max_U + 1e-3)
        dt_diff = 0.22 * (dx_min**2) / (torch.max(Kh_field) + 1.0)
        dt = torch.minimum(torch.minimum(dt_adv, dt_diff), dt_cap)
        if budget_s is not None:
            dt = torch.minimum(dt, torch.tensor(budget_s, device=device))
        u = u + dt*rhs_u; v = v + dt*rhs_v
        u[0,:] = 0.0; v[0,:] = 0.0
        v[-1,:] = 0.95*v[-1,:] + 0.05*(beta10*Vg_t[-1,:])
        u[-1,:] = 0.9*u[-1,:]
        return u, v, float(dt)

    # ---- spin-up to TRUE steady state under the t0 forcing (convergence-checked:
    # the production solver's 800 iterations ~ 48 s pseudo-time are far short of
    # equilibrium; the dynamic march must start from the converged state or the
    # marine-forcing regression would drift for hours)
    dpdr_t, Vg_t, z0_field = forcing_at(dyn.t0_h)
    V10 = beta10 * Vg_t
    u = - V10 * torch.sin(theta_in)
    v =   V10 * torch.cos(theta_in)
    prev_max = -1.0
    for n in range(dyn.spinup_iter):
        u, v, _ = cfl_step(u, v, dpdr_t, Vg_t, z0_field, None)
        if (n + 1) % 1000 == 0:
            cur = float(torch.sqrt(u*u + v*v).max())
            if abs(cur - prev_max) < 0.005:
                break
            prev_max = cur

    # spun-up steady field (earth-relative speed), for frozen-field counterparts
    speed0 = torch.sqrt((u*erx + v*etx + c_x)**2 + (u*ery + v*ety + c_y)**2)

    # ---- physical-time march with sampling every dt_forcing_s
    nt = int(round((dyn.t1_h - dyn.t0_h) * 3600.0 / dyn.dt_forcing_s)) + 1
    N = dyn.sample_ew.numel()
    series = torch.zeros((N, nt), device=device, dtype=torch.float32)
    times_h = [dyn.t0_h + k * dyn.dt_forcing_s / 3600.0 for k in range(nt)]
    snap_want = sorted(dyn.snap_times_h) if getattr(dyn, "snap_times_h", None) else []
    snaps = {}
    for k in range(nt):
        t_h = times_h[k]
        # sample the current state at the earth-fixed points (translation added,
        # magnitude on the polar grid, then interpolated -- as in the pipeline)
        Ux = u * erx + v * etx + c_x
        Uy = u * ery + v * ety + c_y
        speed = torch.sqrt(Ux*Ux + Uy*Uy)
        ewc = args.speed_mph * t_h
        dxs = dyn.sample_ew - ewc                          # miles, +west of storm
        r_tgt = (torch.sqrt(dxs*dxs + dyn.sample_ns*dyn.sample_ns) * MILE_M)[:, None]
        phi_tgt = (torch.atan2(dyn.sample_ns, -dxs) % (2*math.pi))[:, None]
        vals = bilinear_polar(speed, r, phi_g, r_tgt, phi_tgt)
        series[:, k] = torch.where(r_tgt > rmax_out, torch.zeros_like(vals), vals)[:, 0]
        if snap_want and abs(t_h - snap_want[0]) <= 0.5 * dyn.dt_forcing_s / 3600.0:
            snaps[snap_want.pop(0)] = speed.clone()
        if k == nt - 1:
            break
        # advance one forcing interval with the forcing frozen at t_h
        dpdr_t, Vg_t, z0_field = forcing_at(t_h)
        remaining = dyn.dt_forcing_s
        while remaining > 1e-6:
            u, v, took = cfl_step(u, v, dpdr_t, Vg_t, z0_field, remaining)
            remaining -= took

    meta = dict(r=r, phi=phi_g, Rmax=args.rmax_core_km, base_tag="pde_dynamic",
                model_label="PDE (dynamic)", speed0=speed0)
    return series, times_h, snaps, meta

# -------------------------
# Batched dynamic solver: many storms in one (B,Nr,Nphi) tensor
# -------------------------
def bilinear_polar_batch(F, r_src, phi_src, r_tgt, phi_tgt):
    """Batched bilinear_polar: F (B,Nr,Np), r_tgt/phi_tgt (B,N) -> (B,N)."""
    device = F.device
    Bn, Nr, Np = F.shape
    r_src_cpu = r_src.detach().to('cpu')
    i1 = torch.searchsorted(r_src_cpu, r_tgt.detach().reshape(-1).to('cpu'))
    i0 = torch.clamp(i1 - 1, 0, Nr - 2); i1 = torch.clamp(i1, 1, Nr - 1)
    r0 = i0.to(device).reshape(r_tgt.shape); r1 = i1.to(device).reshape(r_tgt.shape)
    r0v = r_src[r0]; r1v = r_src[r1]
    denom = torch.where(r1v == r0v, torch.ones_like(r1v), r1v - r0v)
    wr = (r_tgt - r0v) / denom
    dphi = phi_src[1] - phi_src[0]
    tp = (phi_tgt % (2*math.pi)) / dphi
    p0 = torch.floor(tp).to(torch.int64) % Np
    p1 = (p0 + 1) % Np
    wp = tp - torch.floor(tp)
    flat = F.reshape(Bn, -1)
    def g(ri, pi): return flat.gather(1, ri * Np + pi)
    return ((1-wr)*(1-wp)*g(r0, p0) + wr*(1-wp)*g(r1, p0)
            + (1-wr)*wp*g(r0, p1) + wr*wp*g(r1, p1))

def pde_dynamic_setup_batch(shared, storms, device=None):
    """Grids/constants for a batch of storms sharing one polar grid.

    shared: namespace with lat0, h_bl, beta10, bearing_deg, rmin_km, rmax_km,
            Nr, Nphi, stretch_gamma, Kh_inner, Kh_outer, cfl
    storms: dict of per-storm sequences (len B): dp_hpa, B, rmax_core_km, speed_mph
    Returns the context dict S used by the spinup/march functions."""
    if device is None: device = device_select()
    deg2rad = math.pi/180.0
    MILE_M = 1609.344
    t = lambda x: torch.as_tensor(x, dtype=torch.float32, device=device)
    f = t(2*7.2921159e-5*math.sin(shared.lat0*deg2rad))
    rho = t(1.15)
    dp_Pa = t(storms["dp_hpa"])[:, None, None] * 100.0
    Bh = t(storms["B"])[:, None, None]
    Rmax_m = t(storms["rmax_core_km"])[:, None, None] * 1000.0
    vt = t(storms["speed_mph"])                               # (B,)
    beta10 = t(shared.beta10); h_bl = t(shared.h_bl)
    c_ms = vt[:, None, None] * 0.44704
    theta = t(shared.bearing_deg * deg2rad)
    c_x = c_ms * torch.sin(theta); c_y = c_ms * torch.cos(theta)
    r = build_radial_grid(shared.rmin_km, shared.rmax_km, shared.Nr,
                          gamma=shared.stretch_gamma, device=device)
    phi_g = torch.linspace(0.0, 2*math.pi, shared.Nphi+1, device=device,
                           dtype=torch.float32)[:-1]
    dr = torch.mean(r[1:] - r[:-1]); dphi = phi_g[1] - phi_g[0]
    rr, pp = torch.meshgrid(r, phi_g, indexing='ij')          # (Nr,Np), shared
    dpdr0 = dp_Pa * torch.exp(-(Rmax_m/rr)**Bh) * (Bh*(Rmax_m**Bh)*(rr**(-Bh-1)))
    Kh = torch.where(rr <= 1.5*Rmax_m, t(shared.Kh_inner), t(shared.Kh_outer))
    return dict(
        device=device, Bn=len(storms["speed_mph"]), f=f, rho=rho, vt=vt,
        beta10=beta10, h_bl=h_bl, c_x=c_x, c_y=c_y,
        r=r, phi_g=phi_g, dr=dr, dphi=dphi, rr=rr, rmax_out=r[-1],
        erx=torch.cos(pp), ery=torch.sin(pp), etx=-torch.sin(pp), ety=torch.cos(pp),
        dpdr0=dpdr0, fr=f*rr, theta_in=inflow_angle_rad(rr, Rmax_m), Kh=Kh,
        Kh_max=torch.max(Kh), cfl=shared.cfl,
        dx_min=torch.minimum(dr, r[0]*dphi), dt_cap=t(3.0),
        x_e_mi=(rr*torch.cos(pp))/MILE_M, y_n_mi=(rr*torch.sin(pp))/MILE_M)

def _dyn_forcing_batch(S, dyn, t_h):
    """(dpdr_t, Vg_t, z0_field) at time t_h. dyn.dp_scale(t_h) -> (B,) tensor."""
    if dyn.dp_scale is None:
        dpdr_t = S["dpdr0"]
    else:
        dpdr_t = S["dpdr0"] * dyn.dp_scale(t_h)[:, None, None]
    Vg_t = 0.5*(-S["fr"] + torch.sqrt(S["fr"]**2 + 4.0*S["rr"]*(dpdr_t/S["rho"])))
    z0 = None
    if dyn.z0_fn is not None:
        ewc = S["vt"] * t_h                                    # (B,)
        ew_pt = ewc[:, None, None] - S["x_e_mi"]               # (B,Nr,Np)
        z0 = dyn.z0_fn(ew_pt, S["y_n_mi"].expand_as(ew_pt))
    return dpdr_t, Vg_t, z0

def _dyn_step_batch(S, u, v, dpdr_t, Vg_t, z0, budget_s):
    """One Euler step at the batch-shared CFL dt; returns (u, v, dt_taken_s)."""
    rhs_u, rhs_v, U = physics_terms_dyn(u, v, S["rr"], S["dphi"], S["dr"], S["f"],
                                        dpdr_t, S["rho"], S["Kh"], S["beta10"],
                                        S["h_bl"], z0)
    dt_adv = S["cfl"] * S["dx_min"] / (torch.max(U) + 1e-3)
    dt_diff = 0.22 * S["dx_min"]**2 / (S["Kh_max"] + 1.0)
    dt = torch.minimum(torch.minimum(dt_adv, dt_diff), S["dt_cap"])
    if budget_s is not None:
        dt = torch.minimum(dt, torch.tensor(budget_s, device=S["device"]))
    u = u + dt*rhs_u; v = v + dt*rhs_v
    u[..., 0, :] = 0.0; v[..., 0, :] = 0.0
    v[..., -1, :] = 0.95*v[..., -1, :] + 0.05*(S["beta10"]*Vg_t[..., -1, :])
    u[..., -1, :] = 0.9*u[..., -1, :]
    return u, v, float(dt)

def pde_dynamic_spinup_batch(S, dyn):
    """Converged spin-up under the forcing at dyn.t0_h; per-storm convergence
    (every storm's maxU must drift < 0.005 m/s per 1000 iters)."""
    dpdr_t, Vg_t, z0 = _dyn_forcing_batch(S, dyn, dyn.t0_h)
    V10 = S["beta10"] * Vg_t
    u = -V10 * torch.sin(S["theta_in"])
    v = V10 * torch.cos(S["theta_in"])
    prev = None
    for n in range(dyn.spinup_iter):
        u, v, _ = _dyn_step_batch(S, u, v, dpdr_t, Vg_t, z0, None)
        if (n + 1) % 1000 == 0:
            cur = torch.amax(torch.sqrt(u*u + v*v), dim=(-2, -1))    # (B,)
            if prev is not None and float((cur - prev).abs().max()) < 0.005:
                break
            prev = cur
    return u, v

def pde_dynamic_march_batch(S, dyn, u, v):
    """Physical-time march from state (u,v) at dyn.t0_h, sampling every forcing
    step at the earth-fixed points. Returns (series (B,N,nt), times_h, speed0)
    where speed0 is the (B,Nr,Np) earth-relative speed at t0 (for frozen-field
    products) and series is earth-relative speed (m/s) at the sample points."""
    MILE_M = 1609.344
    nt = int(round((dyn.t1_h - dyn.t0_h) * 3600.0 / dyn.dt_forcing_s)) + 1
    Bn, N = S["Bn"], dyn.sample_ew.numel()
    series = torch.zeros((Bn, N, nt), device=S["device"], dtype=torch.float32)
    times_h = [dyn.t0_h + k * dyn.dt_forcing_s / 3600.0 for k in range(nt)]
    speed0 = None
    for k in range(nt):
        t_h = times_h[k]
        Ux = u*S["erx"] + v*S["etx"] + S["c_x"]
        Uy = u*S["ery"] + v*S["ety"] + S["c_y"]
        speed = torch.sqrt(Ux*Ux + Uy*Uy)
        if k == 0:
            speed0 = speed.clone()
        ewc = S["vt"] * t_h                                    # (B,)
        dxs = dyn.sample_ew[None, :] - ewc[:, None]            # (B,N) +west of storm
        ns2 = dyn.sample_ns[None, :].expand(Bn, -1)
        r_tgt = torch.sqrt(dxs*dxs + ns2*ns2) * MILE_M
        phi_tgt = torch.atan2(ns2, -dxs) % (2*math.pi)
        vals = bilinear_polar_batch(speed, S["r"], S["phi_g"], r_tgt, phi_tgt)
        series[:, :, k] = torch.where(r_tgt > S["rmax_out"], torch.zeros_like(vals), vals)
        if k == nt - 1:
            break
        dpdr_t, Vg_t, z0 = _dyn_forcing_batch(S, dyn, t_h)
        remaining = dyn.dt_forcing_s
        while remaining > 1e-6:
            u, v, took = _dyn_step_batch(S, u, v, dpdr_t, Vg_t, z0, remaining)
            remaining -= took
    return series, times_h, speed0

# -------------------------
# Holland parametric wind (with asymmetry via translation + inflow)
# -------------------------
def holland_asym_marine(args, device=None):
    if device is None: device = device_select()
    deg2rad = math.pi/180.0
    phi0 = args.lat0 * deg2rad
    Omega = 7.2921159e-5
    f = torch.tensor(2*Omega*math.sin(phi0), device=device, dtype=torch.float32)
    rho = torch.tensor(1.15, device=device, dtype=torch.float32)
    dp_Pa = torch.tensor(args.dp_hpa * 100.0, device=device, dtype=torch.float32)
    B = torch.tensor(args.B, device=device, dtype=torch.float32)
    Rmax_m = torch.tensor(args.rmax_core_km*1000.0, device=device, dtype=torch.float32)
    beta10 = torch.tensor(args.beta10, device=device, dtype=torch.float32)
    # Translation
    mph_to_ms = 0.44704
    c_ms = torch.tensor(args.speed_mph * mph_to_ms, device=device, dtype=torch.float32)
    theta = torch.tensor(args.bearing_deg * deg2rad, device=device, dtype=torch.float32)
    c_x = c_ms * torch.sin(theta); c_y = c_ms * torch.cos(theta)
    # Grid
    r = build_radial_grid(args.rmin_km, args.rmax_km, args.Nr, gamma=args.stretch_gamma, device=device)
    phi_g = torch.linspace(0.0, 2*math.pi, args.Nphi+1, device=device, dtype=torch.float32)[:-1]
    rr, pp = torch.meshgrid(r, phi_g, indexing='ij')
    erx = torch.cos(pp); ery = torch.sin(pp)
    etx = -torch.sin(pp); ety =  torch.cos(pp)
    # Holland gradient wind
    exp_term = torch.exp(- (Rmax_m / rr)**B)
    dpdr = dp_Pa * exp_term * (B * (Rmax_m**B) * (rr**(-B-1)))
    fr = f * rr
    Vg = 0.5*(-fr + torch.sqrt(fr**2 + 4.0*rr*(dpdr/rho)))
    # BL vector with inflow
    theta_in = inflow_angle_rad(rr, Rmax_m)
    V10 = beta10 * Vg
    u = - V10 * torch.sin(theta_in)
    v =   V10 * torch.cos(theta_in)
    # Earth-relative & speed
    Ux = u * erx + v * etx
    Uy = u * ery + v * ety
    Ux_er = Ux + c_x; Uy_er = Uy + c_y
    speed_ms = torch.sqrt(Ux_er**2 + Uy_er**2)
    # Apply surface roughness effects if provided
    speed_ms = apply_roughness_effects(speed_ms, r, phi_g, args, device, 'holland')
    
    meta = dict(r=r, phi=phi_g, Rmax=args.rmax_core_km, base_tag="holland_marine", model_label="Holland (marine)")
    return speed_ms, meta

# -------------------------
# Willoughby-type parametric wind (with asymmetry)
# -------------------------
def willoughby_asym_marine(args, device=None):
    if device is None: device = device_select()
    deg2rad = math.pi/180.0
    phi0 = args.lat0 * deg2rad
    Omega = 7.2921159e-5
    f = torch.tensor(2*Omega*math.sin(phi0), device=device, dtype=torch.float32)
    rho = torch.tensor(1.15, device=device, dtype=torch.float32)
    dp_Pa = torch.tensor(args.dp_hpa * 100.0, device=device, dtype=torch.float32)
    B = torch.tensor(args.B, device=device, dtype=torch.float32)
    Rmax_m = torch.tensor(args.rmax_core_km*1000.0, device=device, dtype=torch.float32)
    beta10 = torch.tensor(args.beta10, device=device, dtype=torch.float32)
    # Translation
    mph_to_ms = 0.44704
    c_ms = torch.tensor(args.speed_mph * mph_to_ms, device=device, dtype=torch.float32)
    theta = torch.tensor(args.bearing_deg * deg2rad, device=device, dtype=torch.float32)
    c_x = c_ms * torch.sin(theta); c_y = c_ms * torch.cos(theta)
    # Grid
    r = build_radial_grid(args.rmin_km, args.rmax_km, args.Nr, gamma=args.stretch_gamma, device=device)
    phi_g = torch.linspace(0.0, 2*math.pi, args.Nphi+1, device=device, dtype=torch.float32)[:-1]
    rr, pp = torch.meshgrid(r, phi_g, indexing='ij')
    erx = torch.cos(pp); ery = torch.sin(pp)
    etx = -torch.sin(pp); ety =  torch.cos(pp)
    # Estimate Vmax at Rmax from Holland gradient wind (for consistency with dp/B)
    # dpdr at r=Rmax
    dpdr_R = dp_Pa * torch.exp(torch.tensor(-1.0, device=device)) * (B / Rmax_m)
    fr_R = f * Rmax_m
    Vg_R = 0.5*(-fr_R + torch.sqrt(fr_R**2 + 4.0*Rmax_m*(dpdr_R/rho)))
    Vmax = torch.clamp(Vg_R, min=0.0)
    # Willoughby-like axisymmetric profile (smooth inner power + outer power)
    # Defaults chosen for reasonable shape if not provided
    n_inner = torch.tensor(getattr(args, 'willo_n', 0.6), device=device)
    m_outer = torch.tensor(getattr(args, 'willo_m', 0.5), device=device)
    s = rr / Rmax_m
    V_inner = Vmax * torch.clamp(s, min=1e-6) ** n_inner
    V_outer = Vmax * torch.clamp(s, min=1e-6) ** (-m_outer)
    blend = torch.sigmoid((rr - Rmax_m) / (0.12*Rmax_m + 1.0))
    Vaxis = (1.0 - blend)*V_inner + blend*V_outer
    # BL inflow + reduction
    theta_in = inflow_angle_rad(rr, Rmax_m)
    V10 = beta10 * Vaxis
    u = - V10 * torch.sin(theta_in)
    v =   V10 * torch.cos(theta_in)
    # Earth-relative
    Ux = u * erx + v * etx
    Uy = u * ery + v * ety
    Ux_er = Ux + c_x; Uy_er = Uy + c_y
    speed_ms = torch.sqrt(Ux_er**2 + Uy_er**2)
    # Apply surface roughness effects if provided
    speed_ms = apply_roughness_effects(speed_ms, r, phi_g, args, device, 'willoughby')
    
    meta = dict(r=r, phi=phi_g, Rmax=args.rmax_core_km, base_tag="willoughby_marine", model_label="Willoughby (marine)")
    return speed_ms, meta

# -------------------------
# Regrid & plot (kt only)
# -------------------------
def regrid_and_plot(args, speed_ms, meta, outdir):
    device = speed_ms.device
    r = meta['r']; phi = meta['phi']
    # Refined polar grid
    r2 = build_radial_grid(args.rmin_km, args.rmax_km, args.Nr2, gamma=args.stretch_gamma, device=device)
    phi2 = torch.linspace(0.0, 2*math.pi, args.Nphi2+1, device=device, dtype=torch.float32)[:-1]
    rr2, pp2 = torch.meshgrid(r2, phi2, indexing='ij')
    # Cartesian grid
    rmax_km_out = float((r[-1]/1000.0).item())
    x_lin = torch.arange(-rmax_km_out, rmax_km_out + args.cart_step_km, args.cart_step_km, device=device)
    y_lin = torch.arange(-rmax_km_out, rmax_km_out + args.cart_step_km, args.cart_step_km, device=device)
    XX, YY = torch.meshgrid(x_lin, y_lin, indexing='xy')
    R_here = torch.sqrt((XX*1000.0)**2 + (YY*1000.0)**2)
    Phi_here = (torch.atan2(YY, XX) + 2*math.pi) % (2*math.pi)
    # Map polar→polar (refine) → Cartesian
    spd_ms_ref = bilinear_polar(speed_ms, r, phi, rr2, pp2)
    spd_ms_cart = bilinear_polar(spd_ms_ref, r2, phi2, R_here, Phi_here)
    outside = (R_here < r2[0]) | (R_here > r2[-1])
    spd_ms_cart[outside] = torch.nan
    # To numpy, convert to kt
    kt = 1.9438444924406046
    Z = (spd_ms_cart * kt).detach().cpu().numpy()
    XX_np = XX.detach().cpu().numpy(); YY_np = YY.detach().cpu().numpy()
    # Levels (kt)
    vmax = np.nanmax(Z)
    step = args.level_step
    lev = np.arange(args.kt_min, int(step*np.ceil(vmax/step))+step, step)
    # Scales
    lw = 1.1 * args.line_scale
    fs = 10  * args.font_scale
    # Titles and filenames
    name = args.storm_name.strip()
    date = args.storm_date.strip() if args.storm_date else ""
    slug = slugify(f"{name}-{date}" if date else name) or "storm"
    model_label = meta.get('model_label', 'PDE (marine)')
    base_tag = meta.get('base_tag', 'pde_marine')
    base = f"{slug}_{base_tag}"
    title_suffix = f"{name}" + (f" ({date})" if date else "")
    extra = args.title_extra.strip()
    # FULL
    fig, ax = plt.subplots(figsize=(args.full_fig_in, args.full_fig_in), constrained_layout=True)
    data = np.ma.masked_where(np.isnan(Z) | (Z < args.kt_min), Z)
    CS = ax.contour(XX_np, YY_np, data, levels=lev, linewidths=lw)
    ax.clabel(CS, inline=True, fontsize=fs, fmt="%.0f kt")
    ax.set_aspect('equal'); ax.grid(True, linestyle=':')
    ax.set_xlabel("x (km)"); ax.set_ylabel("y (km)")
    ax.set_title(f"Hurricane {title_suffix} — {model_label}, isotachs ≥{args.kt_min:.0f} kt{(' — '+extra) if extra else ''}")
    full_path = Path(outdir) / f"{base}_full_kt.png"
    fig.savefig(full_path, dpi=args.dpi_full)
    # CORE zoom
    Rmax = float(meta['Rmax'])
    fig, ax = plt.subplots(figsize=(args.core_fig_in, args.core_fig_in), constrained_layout=True)
    CS2 = ax.contour(XX_np, YY_np, data, levels=lev, linewidths=lw)
    ax.clabel(CS2, inline=True, fontsize=fs, fmt="%.0f kt")
    ax.set_aspect('equal'); ax.grid(True, linestyle=':')
    ax.set_xlim(-args.core_window_km, args.core_window_km)
    ax.set_ylim(-args.core_window_km, args.core_window_km)
    try:
        ax_top = ax.secondary_xaxis('top', functions=(lambda x: x/Rmax, lambda s: s*Rmax))
        ax_right = ax.secondary_yaxis('right', functions=(lambda y: y/Rmax, lambda s: s*Rmax))
        ax_top.set_xlabel("x / Rmax"); ax_right.set_ylabel("y / Rmax")
    except Exception:
        pass
    ax.set_xlabel("x (km)"); ax.set_ylabel("y (km)")
    ax.set_title(f"{name} core — {model_label}, isotachs ≥{args.kt_min:.0f} kt{(' — '+extra) if extra else ''}")
    core_path = Path(outdir) / f"{base}_core_kt.png"
    fig.savefig(core_path, dpi=args.dpi_core)
    return full_path, core_path

# -------------------------
# Comparison utilities
# -------------------------
def _cartesian_field(args, speed_ms, meta, shared=None):
    device = speed_ms.device
    r = meta['r']; phi = meta['phi']
    # Build or reuse refined polar + Cartesian grids to ensure alignment across models
    if shared is None:
        r2 = build_radial_grid(args.rmin_km, args.rmax_km, args.Nr2, gamma=args.stretch_gamma, device=device)
        phi2 = torch.linspace(0.0, 2*math.pi, args.Nphi2+1, device=device, dtype=torch.float32)[:-1]
        rmax_km_out = float((r[-1]/1000.0).item())
        x_lin = torch.arange(-rmax_km_out, rmax_km_out + args.cart_step_km, args.cart_step_km, device=device)
        y_lin = torch.arange(-rmax_km_out, rmax_km_out + args.cart_step_km, args.cart_step_km, device=device)
        XX, YY = torch.meshgrid(x_lin, y_lin, indexing='xy')
        shared = dict(r2=r2, phi2=phi2, XX=XX, YY=YY)
    else:
        r2 = shared['r2']; phi2 = shared['phi2']; XX = shared['XX']; YY = shared['YY']
    rr2, pp2 = torch.meshgrid(r2, phi2, indexing='ij')
    R_here = torch.sqrt((XX*1000.0)**2 + (YY*1000.0)**2)
    Phi_here = (torch.atan2(YY, XX) + 2*math.pi) % (2*math.pi)
    spd_ms_ref = bilinear_polar(speed_ms, r, phi, rr2, pp2)
    spd_ms_cart = bilinear_polar(spd_ms_ref, r2, phi2, R_here, Phi_here)
    outside = (R_here < r2[0]) | (R_here > r2[-1])
    spd_ms_cart[outside] = torch.nan
    kt = 1.9438444924406046
    Z = (spd_ms_cart * kt).detach().cpu().numpy()
    XX_np = XX.detach().cpu().numpy(); YY_np = YY.detach().cpu().numpy()
    return Z, XX_np, YY_np, shared

def _compute_R34_km(XX_np, YY_np, Z, thr=34.0):
    mask = np.isfinite(Z) & (Z >= thr)
    if not np.any(mask):
        return float('nan')
    R = np.sqrt(XX_np**2 + YY_np**2)
    return float(np.nanmax(np.where(mask, R, np.nan)))

def _willo_with_m(args, m_value, device):
    ns = argparse.Namespace(**vars(args))
    setattr(ns, 'willo_m', float(m_value))
    return willoughby_asym_marine(ns, device=device)

def _fit_willoughby_m_to_R34(args, target_R34_km, shared, device, m_lo=0.2, m_hi=2.5, iters=18):
    best_m = None; best_Z = None; best_meta = None
    for _ in range(iters):
        m_mid = 0.5*(m_lo + m_hi)
        spd_w, meta_w = _willo_with_m(args, m_mid, device=device)
        Zw, XX, YY, _ = _cartesian_field(args, spd_w, meta_w, shared=shared)
        r34 = _compute_R34_km(XX, YY, Zw, thr=34.0)
        if not np.isfinite(r34):
            r34 = 0.0
        # If current radius is larger than target, increase m to steepen decay
        if r34 > target_R34_km:
            m_lo = m_mid
        else:
            m_hi = m_mid
        best_m, best_Z, best_meta = m_mid, Zw, meta_w
    return float(best_m), best_Z, best_meta

def _plot_isotachs(ax, XX_np, YY_np, Z, args, title, vmin=None, vmax=None):
    step = args.level_step
    vmax_data = np.nanmax(Z) if vmax is None else vmax
    levels = np.arange(args.kt_min, int(step*np.ceil(vmax_data/step))+step, step)
    data = np.ma.masked_where(np.isnan(Z) | (Z < args.kt_min), Z)
    CS = ax.contour(XX_np, YY_np, data, levels=levels, linewidths=1.1*args.line_scale)
    ax.clabel(CS, inline=True, fontsize=10*args.font_scale, fmt="%.0f kt")
    ax.set_aspect('equal'); ax.grid(True, linestyle=':')
    ax.set_xlabel("x (km)"); ax.set_ylabel("y (km)")
    ax.set_title(title)

def compare_models(args):
    device = device_select()
    # Generate three windfields
    spd_pde, meta_pde = pde_steady_marine(args, device=device)
    spd_hol, meta_hol = holland_asym_marine(args, device=device)
    # Compute on shared Cartesian grid
    Zp, XX, YY, shared = _cartesian_field(args, spd_pde, meta_pde, shared=None)
    Zh, XX, YY, _       = _cartesian_field(args, spd_hol, meta_hol, shared=shared)
    # Anchor Willoughby to match PDE R34 size
    target_r34 = _compute_R34_km(XX, YY, Zp, thr=34.0)
    m_fit, Zw, meta_wil = _fit_willoughby_m_to_R34(args, target_r34, shared, device)
    # Common vmax for consistent levels
    vmax_all = np.nanmax([np.nanmax(Zp), np.nanmax(Zh), np.nanmax(Zw)])
    # Figure: three panels
    name = args.storm_name.strip(); date = args.storm_date.strip() if args.storm_date else ""
    title_suffix = f"{name}" + (f" ({date})" if date else "")
    extra = args.title_extra.strip()
    fig, axes = plt.subplots(1, 3, figsize=(min(54, 3*args.core_fig_in), args.core_fig_in), constrained_layout=True)
    _plot_isotachs(axes[0], XX, YY, Zp, args, f"{title_suffix} — PDE (marine){(' — '+extra) if extra else ''}", vmax=vmax_all)
    _plot_isotachs(axes[1], XX, YY, Zh, args, f"{title_suffix} — Holland (marine){(' — '+extra) if extra else ''}", vmax=vmax_all)
    _plot_isotachs(axes[2], XX, YY, Zw, args, f"{title_suffix} — Willoughby (marine, R34‑matched m={m_fit:.2f}){(' — '+extra) if extra else ''}", vmax=vmax_all)
    slug = slugify(f"{name}-{date}" if date else name) or "storm"
    outdir = Path(args.out); outdir.mkdir(parents=True, exist_ok=True)
    compare_path = outdir / f"{slug}_compare3_r34match_kt.png"
    fig.savefig(compare_path, dpi=args.dpi_core)
    # Differences and metrics
    def stats(A, B):
        D = A - B
        m = np.nanmean(D)
        mae = np.nanmean(np.abs(D))
        rmse = np.sqrt(np.nanmean(D**2))
        return dict(mean=m, mae=mae, rmse=rmse)
    hol_stats = stats(Zh, Zp)
    wil_stats = stats(Zw, Zp)
    # Optional diff figure
    vmax_diff = np.nanmax(np.abs(np.concatenate([(Zh-Zp).ravel(), (Zw-Zp).ravel()])))
    vmax_diff = float(np.ceil(max(vmax_diff, 1.0)/5.0)*5.0)
    fig2, axes2 = plt.subplots(1, 2, figsize=(min(36, 2*args.core_fig_in), args.core_fig_in), constrained_layout=True)
    for ax, D, title in [(axes2[0], Zh-Zp, f"Holland − PDE  (RMSE {hol_stats['rmse']:.1f} kt)"),
                         (axes2[1], Zw-Zp, f"Willoughby − PDE  (RMSE {wil_stats['rmse']:.1f} kt)")]:
        data = np.ma.masked_where(np.isnan(D), D)
        levels = np.linspace(-vmax_diff, vmax_diff, 17)
        CS = ax.contourf(XX, YY, data, levels=levels, cmap='coolwarm', extend='both')
        ax.contour(XX, YY, data, levels=[0.0], colors='k', linewidths=1)
        ax.set_aspect('equal'); ax.grid(True, linestyle=':')
        ax.set_xlabel("x (km)"); ax.set_ylabel("y (km)")
        ax.set_title(title)
    cbar = fig2.colorbar(CS, ax=axes2.ravel().tolist(), shrink=0.9, label='kt')
    diff_path = outdir / f"{slug}_diffs_r34match_kt.png"
    fig2.savefig(diff_path, dpi=args.dpi_core)
    print(f"[R34] Target (from PDE): {target_r34:.1f} km; Willoughby m_fit={m_fit:.3f}")
    return compare_path, diff_path, hol_stats, wil_stats

# -------------------------
# Main
# -------------------------
def main():
    ap = argparse.ArgumentParser(description="General hurricane isotach generator (PDE marine, MPS‑safe)")
    ap.add_argument("--out", default="out_any", help="output directory")
    ap.add_argument("--storm-name", default="Storm", help="hurricane name for titles/filenames")
    ap.add_argument("--storm-date", default="", help="ISO date like 2004-08-13 (optional but recommended)")
    ap.add_argument("--title-extra", default="BIG contours", help="extra note to append to plot titles")
    ap.add_argument("--windfield", choices=["pde","holland","willoughby"], default="pde", help="windfield model: pde (default), holland, or willoughby (all include asymmetry)")
    ap.add_argument("--compare", action="store_true", help="Generate a 3-panel comparison (PDE, Holland, Willoughby) plus diff maps and metrics")
    # Willoughby parameters (optional overrides)
    ap.add_argument("--willo-n", dest="willo_n", type=float, default=0.6, help="Willoughby inner exponent n (default 0.6)")
    ap.add_argument("--willo-m", dest="willo_m", type=float, default=0.5, help="Willoughby outer exponent m (default 0.5)")
    # Location (for f only)
    ap.add_argument("--lat0", type=float, default=26.6)
    ap.add_argument("--lon0", type=float, default=-82.2)
    # Holland core
    ap.add_argument("--B", type=float, default=1.7)
    ap.add_argument("--rmax-core-km", type=float, default=10.0)
    ap.add_argument("--dp-hpa", type=float, default=72.0)
    # BL / reduction
    ap.add_argument("--beta10", type=float, default=0.80)
    ap.add_argument("--h-bl", type=float, default=500.0)
    # Translation (mph, bearing deg from North clockwise)
    ap.add_argument("--speed-mph", type=float, default=21.0)
    ap.add_argument("--bearing-deg", type=float, default=25.0)
    # Polar grid & solver
    ap.add_argument("--rmin-km", type=float, default=0.5)
    ap.add_argument("--rmax-km", type=float, default=250.0)
    ap.add_argument("--Nr", type=int, default=200)
    ap.add_argument("--Nphi", type=int, default=360)
    ap.add_argument("--stretch-gamma", type=float, default=2.5)
    ap.add_argument("--Kh-inner", dest="Kh_inner", type=float, default=100.0)
    ap.add_argument("--Kh-outer", dest="Kh_outer", type=float, default=250.0)
    ap.add_argument("--iter", type=int, default=800)
    ap.add_argument("--cfl", type=float, default=0.5)
    # Refinement & plotting
    ap.add_argument("--Nr2", type=int, default=480)
    ap.add_argument("--Nphi2", type=int, default=512)
    ap.add_argument("--cart-step-km", type=float, default=0.25)
    ap.add_argument("--core-window-km", type=float, default=100.0)
    ap.add_argument("--kt-min", type=float, default=35.0)
    ap.add_argument("--level-step", type=float, default=5.0)
    ap.add_argument("--line-scale", type=float, default=1.0, help="multiply contour line width (default 1x for thin lines)")
    ap.add_argument("--font-scale", type=float, default=1.0, help="multiply label font size (default 1x for small labels)")
    ap.add_argument("--full-fig-in", type=float, default=28.0, help="full-domain figure width/height in inches")
    ap.add_argument("--core-fig-in", type=float, default=24.0, help="core figure width/height in inches")
    ap.add_argument("--dpi-full", type=int, default=220)
    ap.add_argument("--dpi-core", type=int, default=240)
    # Surface roughness parameters
    ap.add_argument("--z0-img", type=str, default=None, help="path to surface roughness image (e.g., florida_z0_2024.jpeg)")
    ap.add_argument("--lat-min", dest="lat_min", type=float, default=24.2, help="minimum latitude for roughness map")
    ap.add_argument("--lat-max", dest="lat_max", type=float, default=31.2, help="maximum latitude for roughness map")
    ap.add_argument("--lon-min", dest="lon_min", type=float, default=-88.0, help="minimum longitude for roughness map")
    ap.add_argument("--lon-max", dest="lon_max", type=float, default=-79.0, help="maximum longitude for roughness map")
    ap.add_argument("--water-z0-threshold", dest="water_z0_threshold", type=float, default=5e-4, help="z0 threshold below which is considered water")
    ap.add_argument("--roughness-zone-km", dest="roughness_zone_km", type=float, default=100.0, help="radius within which to apply roughness effects")
    args = ap.parse_args()
    device = device_select()
    print(f"[INFO] torch {torch.__version__}  device: {device}  mps_available={torch.backends.mps.is_available()}")
    if args.compare:
        compare_path, diff_path, hol_stats, wil_stats = compare_models(args)
        print(f"[COMPARE] Saved: {compare_path}")
        print(f"[COMPARE] Saved: {diff_path}")
        print(f"[STATS] Holland vs PDE:  mean={hol_stats['mean']:.2f} kt  MAE={hol_stats['mae']:.2f} kt  RMSE={hol_stats['rmse']:.2f} kt")
        print(f"[STATS] Willoughby vs PDE:  mean={wil_stats['mean']:.2f} kt  MAE={wil_stats['mae']:.2f} kt  RMSE={wil_stats['rmse']:.2f} kt")
    else:
        if args.windfield == 'pde':
            spd_ms, meta = pde_steady_marine(args, device=device)
        elif args.windfield == 'holland':
            spd_ms, meta = holland_asym_marine(args, device=device)
        elif args.windfield == 'willoughby':
            spd_ms, meta = willoughby_asym_marine(args, device=device)
        else:
            raise ValueError(f"Unsupported windfield: {args.windfield}")
        outdir = Path(args.out); outdir.mkdir(parents=True, exist_ok=True)
        full_path, core_path = regrid_and_plot(args, spd_ms, meta, outdir)
        print("[DONE]", full_path.resolve())
        print("[DONE]", core_path.resolve())

if __name__ == "__main__":
    main()
