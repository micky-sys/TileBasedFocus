@echo off
set "REPO=g:\My Drive\Micky\WORK\Cursor\TileBasedFocus"
set "KAYA_VISION_POINT_BIN_PATH=%REPO%\KAYA Instruments\Common\bin"
cd /d "%REPO%"
start "" py -3 host\main_capture.py --cti "%KAYA_VISION_POINT_BIN_PATH%\KYFGLibGenTL_vc141.cti"
