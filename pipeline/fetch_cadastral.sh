#!/bin/zsh
# Fetch the FL Dept. of Revenue 2025 statewide cadastral (parcel polygons joined to the
# NAL real-property roll by the county property appraisers). Used by build_exposure_tax.py.
#
# ~2.8 GB zip -> data/ (gitignored). Resumable: re-run to continue a partial download.
cd "$(dirname "$0")/.."
mkdir -p data
URL="https://publicfiles.dep.state.fl.us/otis/gis/data/Cadastral_Statewide.zip"
ZIP="data/Cadastral_Statewide.zip"
echo "Fetching FDOR statewide cadastral -> $ZIP"
curl -L --retry 3 -C - "$URL" -o "$ZIP" -w "http=%{http_code} bytes=%{size_download}\n"
echo "Unpacking -> data/cadastral/"
rm -rf data/cadastral && mkdir -p data/cadastral
unzip -q -o "$ZIP" -d data/cadastral
find data/cadastral -maxdepth 2 \( -name '*.shp' -o -name '*.gdb' \) -print
