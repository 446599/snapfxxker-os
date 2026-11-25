#!/bin/sh
# Runs ON THE PRINTER, after reviewer approval, with a verified staged file.
# Installs configuration only: does not restart Klipper or start motion.
set -eu
stage=/tmp/ace_sensor_seek-reviewed.py
extra=/home/lava/klipper/klippy/extras/ace_sensor_seek.py
cfg=/home/lava/printer_data/config/printer.cfg
include=/home/lava/printer_data/config/ace-sensor-seek.cfg
backup=/home/lava/printer_data/config/sensor-seek-backup-20261107
candidate=a54de6ae9d366ca31b7d373931eaff83ec5a3d60c7ff23f0dcd689ae071a05da
baseline=6e5686a83c178a9be3c8b66cf2da8250fda7bc835dc99e321dc15230125abb7d
[ "$(sha256sum "$stage" | cut -d' ' -f1)" = "$candidate" ]
[ "$(sha256sum "$cfg" | cut -d' ' -f1)" = "$baseline" ]
[ ! -e "$extra" ] && [ ! -e "$include" ] && [ ! -e "$backup" ]
mkdir "$backup"
cp -p "$cfg" "$backup/printer.cfg"
cp "$stage" "$extra"
printf '[ace_sensor_seek]\n' > "$include"
cp -p "$cfg" "$backup/printer.new"
printf '\n[include ace-sensor-seek.cfg]\n' >> "$backup/printer.new"
cp -p "$backup/printer.new" "$cfg"
sha256sum "$cfg" "$extra" "$include" "$backup/printer.cfg"
printf 'Installed; no RESTART or motion performed.\n'
