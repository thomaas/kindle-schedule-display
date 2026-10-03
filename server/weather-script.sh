#!/bin/sh

cd "$(dirname "$0")"

python3 weather-script.py
magick -background white weather-script-output.svg -resize 600x800 -colorspace gray -colors 16 -depth 8 -quality 00 weather-script-output.png
cp -f weather-script-output.png /path/to/web/server/directory/weather-script-output.png
