#!/bin/sh
# Ворота перед публикацией: пушить можно, только если всё зелёное.
#   sh tools/publish_gate.sh /путь/к/приватным_словам.txt
# 1) privacy: фамилии/места семьи, телефоны, почты, ключи, даты;
# 2) демо-дело строится и проходит check;
# 3) идемпотентность: второй прогон демо не меняет ни байта;
# 4) демо-веер собирается.
set -e
cd "$(dirname "$0")/.."
WORDS="${1:?нужен файл со словами, которых не должно быть в репо}"
python3 rod.py privacy . --words "$WORDS" --allow noreply
sh demo/make_demo.sh > /dev/null
A=$(cat demo/case/* | shasum)
sh demo/make_demo.sh > /dev/null
B=$(cat demo/case/* | shasum)
[ "$A" = "$B" ] || { echo "НЕ идемпотентно: второй прогон демо изменил файлы"; exit 1; }
T=$(mktemp -d)
python3 fan/fan_build.py fan/demo/family.json "$T/demo.html" > /dev/null
python3 rod.py --dir demo/case fan "$T/demo2.html" > /dev/null
rm -rf "$T"
echo "ворота: зелёные — можно пушить"
