#!/bin/sh

cd "$(dirname "$0")"

rm schedule-script-output.png

if wget http://192.168.40.191/schedule-script-output.png; then
	eips -g schedule-script-output.png
else
	eips -g weather-image-error.png
fi
