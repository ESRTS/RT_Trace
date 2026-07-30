CPM_PATH="$(python -c \
'from PyInstaller.utils.hooks import get_package_paths; print(get_package_paths("cmsis_pack_manager")[1])')"

rm -rf build dist

pyinstaller \
    src/RT-Trace.py \
    --noconsole \
    --icon ./icon/icon.icns \
    --add-data "Resources/config.ini:." \
    --collect-all pyocd \
    --collect-all numpy \
    --add-data "$CPM_PATH:cmsis_pack_manager" \
    --noconfirm 