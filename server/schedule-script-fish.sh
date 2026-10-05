#!/bin/sh

# 0,30 6-20 * * 1-5,7 sh /home/pi/fishdraw/schedule-script-fish.sh

# Rotation for landscape display: use 90 or -90 depending on how the Kindle is placed
ROTATE=90
TARGET=/home/pi/fishdraw/schedule-script-output.png
SCHEDULE_SCRIPT=/home/pi/kindle-weather-display/scheduleScript/schedule-script.py

cd "$(dirname "$0")"

# The fish is named after the cancelled lessons ("Nonmathematicus musicus").
# Without cancellations fishdraw picks a random fish with a random name.
FISH_NAME=$(python3 "$SCHEDULE_SCRIPT" --fish-name --no-weather)

if [ -n "$FISH_NAME" ]; then
	node fishdraw.js --seed "$FISH_NAME" > schedule-script-output.svg
else
	node fishdraw.js > schedule-script-output.svg
fi
rsvg-convert -z 3 -b white schedule-script-output.svg | magick png:- -flatten -rotate $ROTATE -resize 600x800 -gravity center -background white -extent 600x800 -colorspace Gray -dither FloydSteinberg -colors 16 -depth 8 -define png:color-type=0 $TARGET
cp $TARGET /var/www/html/
