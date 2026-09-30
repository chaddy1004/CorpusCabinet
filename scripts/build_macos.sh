#!/usr/bin/env bash

set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="$project_dir/.venv/bin/python"
pyinstaller_bin="$project_dir/.venv/bin/pyinstaller"
source_icon="$project_dir/src/corpus_cabinet/assets/logo.png"
build_dir="$project_dir/build/macos"
dist_dir="$project_dir/dist"
app_path="$dist_dir/Corpus Cabinet.app"
bundle_id="com.corpuscabinet.app"
export PYINSTALLER_CONFIG_DIR="$build_dir/cache"

if [ ! -x "$python_bin" ] || [ ! -x "$pyinstaller_bin" ]; then
    echo "Run 'uv sync --group dev' before packaging." >&2
    exit 1
fi

if [ ! -f "$source_icon" ]; then
    echo "The application icon is missing: $source_icon" >&2
    exit 1
fi

version="$("$python_bin" -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], "rb"))["project"]["version"])' "$project_dir/pyproject.toml")"
architecture="$(uname -m)"
dmg_path="$dist_dir/CorpusCabinet-$version-macos-$architecture.dmg"
temporary_dir="$(mktemp -d -t corpus-cabinet-package)"
dmg_source="$temporary_dir/dmg"

cleanup() {
    rm -rf "$temporary_dir"
}
trap cleanup EXIT

mkdir -p "$build_dir" "$dist_dir" "$dmg_source"

"$pyinstaller_bin" \
    --noconfirm \
    --clean \
    --windowed \
    --name "Corpus Cabinet" \
    --osx-bundle-identifier "$bundle_id" \
    --icon "$source_icon" \
    --paths "$project_dir/src" \
    --add-data "$project_dir/src/corpus_cabinet/assets:corpus_cabinet/assets" \
    --add-data "$project_dir/src/corpus_cabinet/configs:corpus_cabinet/configs" \
    --exclude-module accelerate \
    --exclude-module cv2 \
    --exclude-module docling \
    --exclude-module docling_core \
    --exclude-module docling_parse \
    --exclude-module pandas \
    --exclude-module safetensors \
    --exclude-module scipy \
    --exclude-module tokenizers \
    --exclude-module torch \
    --exclude-module torchvision \
    --exclude-module transformers \
    --copy-metadata corpus-cabinet \
    --distpath "$dist_dir" \
    --workpath "$build_dir/work" \
    --specpath "$build_dir/spec" \
    "$project_dir/run_desktop.py"

codesign --force --deep --sign - "$app_path"
codesign --verify --deep --strict "$app_path"

ditto "$app_path" "$dmg_source/Corpus Cabinet.app"
ln -s /Applications "$dmg_source/Applications"
ditto "$project_dir/BETA_INSTALL.md" "$dmg_source/BETA_INSTALL.md"
hdiutil create \
    -volname "Corpus Cabinet Beta" \
    -srcfolder "$dmg_source" \
    -ov \
    -format UDZO \
    "$dmg_path"

echo "Created $dmg_path"
