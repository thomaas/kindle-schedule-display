#!/bin/sh

# 0,30 6-20 * * 1-5,7 sh /home/pi/kindle-weather-display/scheduleScript/schedule-script.sh

# Rotation for landscape display: use 90 or -90 depending on how the Kindle is placed
ROTATE=90
TARGET=/home/pi/kindle-weather-display/scheduleScript/schedule-script-output.png

cd "$(dirname "$0")"

python3 schedule-script.py
magick -background white schedule-script-output.svg -rotate $ROTATE -resize 600x800 -colorspace gray -colors 16 -depth 8 -quality 00 $TARGET
cp $TARGET /var/www/html/