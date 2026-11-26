#!/usr/bin/env python3
"""Веер предков — сборщик одного самодостаточного HTML-файла.

Данные о семье лежат отдельно, в JSON (пример — demo/family.json).
Картинки (фото, сканы) — в папке рядом, встраиваются в HTML как base64.

    python3 fan_build.py demo/family.json out.html
    python3 fan_build.py my/family.json out.html --img my/fanimg

Формат JSON — см. README.md, раздел «Веер предков».
"""
import argparse, base64, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def build(D, out, img_dir=None):
    """Собрать HTML из словаря D (meta, people, lines, events). Возвращает размер файла."""
    people = D["people"]
    if "" not in people:
        sys.exit('нужен человек в центре: ключ "" в people')
    imgs = {}
    for path, p in people.items():
        for key in ("ph", "dc"):
            kept = []
            for fn, cap in p.get(key, []):
                if fn not in imgs:
                    fp = os.path.join(img_dir or "", fn)
                    if not img_dir or not os.path.isfile(fp):
                        print(f"  нет картинки {fn} — пропускаю", file=sys.stderr)
                        continue
                    mime = MIME.get(os.path.splitext(fn)[1].lower(), "image/jpeg")
                    with open(fp, "rb") as f:
                        imgs[fn] = f"data:{mime};base64," + base64.b64encode(f.read()).decode()
                kept.append([fn, cap])
            p[key] = kept
    data = dict(
        meta=D.get("meta", {}),
        people=people,
        lines=D.get("lines", []),
        events=[dict(y=y, lane=lane, c=c, t=t) for y, lane, c, t in D.get("events", [])],
        imgs=imgs,
    )
    with open(os.path.join(HERE, "fan_template.html"), encoding="utf-8") as f:
        tpl = f.read()
    with open(out, "w", encoding="utf-8") as f:
        f.write(tpl.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False)))
    return os.path.getsize(out), len(people), len(imgs)


def main():
    ap = argparse.ArgumentParser(description="Собрать «Веер предков» из JSON")
    ap.add_argument("data", help="JSON с людьми, линиями и событиями")
    ap.add_argument("out", help="куда записать HTML")
    ap.add_argument("--img", help="папка с картинками (по умолчанию fanimg/ рядом с JSON)")
    a = ap.parse_args()
    with open(a.data, encoding="utf-8") as f:
        D = json.load(f)
    img_dir = a.img or os.path.join(os.path.dirname(os.path.abspath(a.data)), "fanimg")
    size, n, ni = build(D, a.out, img_dir)
    print("ok", a.out, size, "байт,", n, "чел.,", ni, "картинок")


if __name__ == "__main__":
    main()
