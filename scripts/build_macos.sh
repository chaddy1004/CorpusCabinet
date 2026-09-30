#!/usr/bin/env bash

set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="$project_dir/.venv/bin/python"
pyinstaller_bin="$project_dir/.venv/bin/pyinstaller"
source_icon="$project_dir/src/corpus_cabinet/assets/logo.png"
models_dir="$project_dir/.reader_models"
docling_parse_dir="$($python_bin -c 'import os, docling_parse; print(os.path.dirname(docling_parse.__file__))')"
torchvision_dir="$($python_bin -c 'import os, torchvision; print(os.path.dirname(torchvision.__file__))')"
build_dir="$project_dir/build/macos"
dist_dir="$project_dir/dist"
app_path="$dist_dir/Corpus Cabinet.app"
bundle_id="com.corpuscabinet.app"
export PYINSTALLER_CONFIG_DIR="$build_dir/cache"

if [ ! -x "$python_bin" ] || [ ! -x "$pyinstaller_bin" ]; then
    echo "Run 'uv sync --extra reader --group dev' before packaging." >&2
    exit 1
fi

if [ ! -f "$source_icon" ]; then
    echo "The application icon is missing: $source_icon" >&2
    exit 1
fi

if [ ! -d "$models_dir" ]; then
    echo "The offline Reader models are missing: $models_dir" >&2
    exit 1
fi

version="$($python_bin -c 'import importlib.metadata; print(importlib.metadata.version("corpus-cabinet"))')"
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
    --add-data "$models_dir:.reader_models" \
    --add-data "$docling_parse_dir/pdf_resources:docling_parse/pdf_resources" \
    --add-binary "$torchvision_dir/_C_stable.so:torchvision" \
    --add-binary "$torchvision_dir/image_stable.so:torchvision" \
    --add-binary "$torchvision_dir/.dylibs:torchvision/.dylibs" \
    --hidden-import docling.models.plugins.defaults \
    --hidden-import docling.models.stages.layout.layout_model \
    --hidden-import docling.models.stages.layout.layout_object_detection_model \
    --hidden-import docling.experimental.models.table_crops_layout_model \
    --hidden-import docling.models.inference_engines.object_detection.transformers_engine \
    --copy-metadata corpus-cabinet \
    --copy-metadata docling \
    --copy-metadata docling-slim \
    --copy-metadata docling-core \
    --copy-metadata transformers \
    --copy-metadata torch \
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
