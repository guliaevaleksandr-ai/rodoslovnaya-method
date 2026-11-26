#!/usr/bin/env python3
"""rod — шаги генеалогического дела кодом. Повторный запуск не плодит дублей.

Дело — папка с реестрами (см. templates/):
  person-register.csv · artifact-register.csv · claims-register.csv ·
  relationship-register.csv · search-log.csv · research-state.md

Команды (папка дела — --dir, по умолчанию текущая):
  rod.py init                         создать пустое дело из шаблонов
  rod.py log  --query Q --source S [--status found --persons P-0001 ...]
                                      записать запрос в журнал; тот же запрос
                                      (запрос+источник+фильтры) обновляется, а не дублируется
  rod.py add person|artifact|claim|rel --set поле=значение ...
                                      занести запись; совпадающая по ключу — дополняется
  rod.py check                        целостность реестров (ссылки, дубли, доказательность)
  rod.py next [--depth 5]             пустые места в дереве + открытая очередь
  rod.py fan OUT.html [--root P-0001] собрать «Веер предков» из реестров
  rod.py privacy PATH [--words FILE]  grep перед публикацией: слова из файла,
                                      телефоны, почты, ключи

Только стандартная библиотека Python 3.
"""
import argparse, csv, json, os, re, shutil, sys, tempfile
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "fan"))

REG = {  # тип → (файл, префикс id, колонка id)
    "person":   ("person-register.csv", "P", "person_id"),
    "artifact": ("artifact-register.csv", "A", "artifact_id"),
    "claim":    ("claims-register.csv", "C", "claim_id"),
    "rel":      ("relationship-register.csv", "R", "relationship_id"),
    "search":   ("search-log.csv", "S", "search_id"),
}
# id: P-0001, а также S-1126T-03 (дата + метка параллельной сессии — чтобы два писателя не столкнулись)
ID_RE = re.compile(r"\b([PACRS])-(\d{3,}(?:[A-Za-z]*-\d+)?)\b")

# поля-списки (значения через «;») — при дополнении объединяются
LIST_FIELDS = {
    "source_artifact_ids", "person_ids", "related_claim_ids", "subject_person_ids", "artifact_ids",
    "supporting_independence_groups", "contradicting_artifact_ids", "claim_ids",
    "supporting_artifact_ids", "independence_groups", "target_person_ids",
}
# поля журнала, которые законно меняются при повторном запросе (капча пройдена → найдено)
LOG_MUTABLE = {"timestamp", "result_status", "blocker", "captcha_attempts", "next_step", "research_goal"}


# ---------- общие ----------
def norm(s):
    return re.sub(r"\s+", " ", (s or "").lower().replace("ё", "е")).strip(" .,;")


def split_list(v):
    return [x.strip() for x in re.split(r"[;|]", v or "") if x.strip()]


def ids_in(v, prefix=None):
    return [m.group(0) for m in ID_RE.finditer(v or "") if prefix is None or m.group(1) == prefix]


class Case:
    def __init__(self, d):
        self.d = d
        self.data, self.hdr, self.broken = {}, {}, []
        for t, (fn, _, _) in REG.items():
            path = os.path.join(d, fn)
            if not os.path.exists(path):
                self.data[t], self.hdr[t] = [], None
                continue
            with open(path, encoding="utf-8", newline="") as f:
                r = csv.DictReader(f)
                self.hdr[t] = r.fieldnames
                self.data[t] = []
                for row in r:
                    row = dict(row)
                    extra = row.pop(None, None)  # лишние запятые без кавычек → колонки съехали
                    if extra:
                        self.broken.append(f"{fn}: строка {r.line_num} ({row.get(REG[t][2])}) — лишних полей {len(extra)}, колонки съехали: {extra}")
                    self.data[t].append({k: (v or "") for k, v in row.items()})

    def need(self, t):
        if self.hdr[t] is None:
            sys.exit(f"нет {REG[t][0]} в {self.d} — начните с: rod.py init")

    def save(self, t):
        if any(x.startswith(REG[t][0]) for x in self.broken):
            sys.exit(f"{REG[t][0]} повреждён (см. rod.py check) — не записываю, чтобы не потерять данные")
        fn = os.path.join(self.d, REG[t][0])
        fd, tmp = tempfile.mkstemp(dir=self.d, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=self.hdr[t], extrasaction="ignore")
            w.writeheader()
            w.writerows(self.data[t])
        os.replace(tmp, fn)  # атомарно: оборванная запись не портит реестр

    def next_id(self, t):
        _, pre, col = REG[t]
        nums = [int(m.group(2)) for row in self.data[t] for m in [ID_RE.fullmatch(row.get(col, ""))]
                if m and m.group(2).isdigit()]
        return f"{pre}-{(max(nums) if nums else 0) + 1:04d}"

    def index(self, t):
        return {row[REG[t][2]]: row for row in self.data[t]}


def key_of(t, row):
    """Ключ «та же самая запись» для каждого реестра."""
    if t == "person":
        return (norm(row.get("preferred_label")), norm(row.get("birth_estimate"))[:4])
    if t == "artifact":
        for f in ("archival_reference", "source_url_or_path", "source_title"):
            if row.get(f):
                return (f, norm(row[f]))
        return None
    if t == "claim":
        return (norm(row.get("claim_text")),)
    if t == "rel":
        return (row.get("person_a_id"), norm(row.get("relationship_type")), row.get("person_b_id"))
    if t == "search":
        return (norm(row.get("query")), norm(row.get("source_or_domain")), norm(row.get("filters")))


def upsert(case, t, fields, mutable=(), force=False):
    """Найти по ключу и дополнить, иначе добавить. Возвращает (id, что_сделано)."""
    case.need(t)
    unknown = [k for k in fields if k not in case.hdr[t]]
    if unknown:
        sys.exit(f"нет таких колонок в {REG[t][0]}: {', '.join(unknown)}\nесть: {', '.join(case.hdr[t])}")
    col = REG[t][2]
    k = key_of(t, fields)
    if k is None or not any(k):
        sys.exit("не хватает полей для ключа записи (см. key_of в rod.py)")
    for row in case.data[t]:
        if key_of(t, row) != k:
            continue
        changed, kept = [], []
        for f, v in fields.items():
            old = row.get(f, "")
            if not v or v == old or norm(v) == norm(old):  # то же самое с точностью до регистра/пробелов
                continue
            if f in LIST_FIELDS:
                merged = split_list(old) + [x for x in split_list(v) if x not in split_list(old)]
                if merged != split_list(old):
                    row[f] = ";".join(merged); changed.append(f)
            elif not old or f in mutable or force:
                row[f] = v; changed.append(f)
            else:
                kept.append(f"{f}: в реестре «{old}», пришло «{v}»")
        for x in kept:
            print(f"  ! не затираю {x} (нужно — --force)", file=sys.stderr)
        if changed:
            case.save(t)
        return row[col], ("дополнено: " + ", ".join(changed)) if changed else "без изменений"
    row = {h: "" for h in case.hdr[t]}
    row.update({f: v for f, v in fields.items() if v})
    row[col] = case.next_id(t)
    case.data[t].append(row)
    case.save(t)
    return row[col], "новая запись"


def parse_sets(pairs):
    out = {}
    for p in pairs or []:
        if "=" not in p:
            sys.exit(f"--set ждёт поле=значение, пришло: {p}")
        k, v = p.split("=", 1)
        out[k.strip()] = v.strip()
    return out


# ---------- команды ----------
def cmd_init(a):
    os.makedirs(a.dir, exist_ok=True)
    src = os.path.join(HERE, "templates")
    made = []
    for fn in [r[0] for r in REG.values()] + ["research-state.md"]:
        dst = os.path.join(a.dir, fn)
        if os.path.exists(dst):
            continue
        if fn.endswith(".csv"):  # только заголовок, без демо-строк
            with open(os.path.join(src, fn), encoding="utf-8") as f:
                hdr = f.readline()
            with open(dst, "w", encoding="utf-8") as f:
                f.write(hdr)
        else:
            shutil.copy(os.path.join(src, fn), dst)
        made.append(fn)
    print("создано:", ", ".join(made) if made else "ничего — дело уже есть")


def cmd_log(a):
    case = Case(a.dir)
    f = dict(query=a.query, source_or_domain=a.source, filters=a.filters or "",
             timestamp=a.date or date.today().isoformat(), target_person_ids=a.persons or "",
             research_goal=a.goal or "", language_locale=a.lang, result_status=a.status,
             artifact_ids=a.artifacts or "", blocker=a.blocker or "", next_step=a.next or "")
    if a.status not in STATUSES:
        sys.exit(f"--status из: {', '.join(STATUSES)}")
    for row in case.data["search"]:  # капча пройдена → старый блокер больше не актуален
        if key_of("search", row) == key_of("search", f) and not a.status.startswith("blocked") and row.get("blocker"):
            row["blocker"] = ""
            if not f["blocker"]:
                case.save("search")
    sid, what = upsert(case, "search", f, mutable=LOG_MUTABLE)
    print(sid, what)


STATUSES = ["found", "no-match", "too-many", "blocked-captcha", "blocked-login", "site-down", "partial"]


def cmd_add(a):
    case = Case(a.dir)
    rid, what = upsert(case, a.type, parse_sets(a.set), force=a.force)
    print(rid if a.quiet else f"{rid} {what}")


def cmd_check(a):
    case = Case(a.dir)
    err, warn = list(case.broken), []
    idx = {t: case.index(t) for t in REG}
    known = {REG[t][1]: set(idx[t]) for t in REG}
    # 1. дубли id и дубли по ключу
    for t, (fn, pre, col) in REG.items():
        seen, keys = set(), {}
        for row in case.data[t]:
            i = row.get(col, "")
            m = ID_RE.fullmatch(i)
            if not m or m.group(1) != pre:
                err.append(f"{fn}: плохой id «{i}»")
            if i in seen:
                err.append(f"{fn}: id {i} повторяется")
            seen.add(i)
            k = key_of(t, row)
            if k and any(k):
                if k in keys:
                    warn.append(f"{fn}: {keys[k]} и {i} похожи на одну запись (ключ {k})")
                keys.setdefault(k, i)
    # 2. висячие ссылки
    for t, (fn, _, col) in REG.items():
        for row in case.data[t]:
            for f, v in row.items():
                if f == col or f in ("notes", "rationale", "merge_or_split_rationale", "relatives_as_reported",
                                     "summary", "reliability_notes", "next_step", "next_action", "claim_text"):
                    continue
                for ref in ids_in(v):
                    if ref not in known[ref[0]]:
                        err.append(f"{fn}: {row[col]}.{f} ссылается на несуществующий {ref}")
    # 3. доказательность
    strong = {"corroborated", "confirmed", "document"}
    for c in case.data["claim"]:
        arts = ids_in(c.get("artifact_ids"), "A")
        if not arts:
            err.append(f"claims: {c['claim_id']} без единого артефакта")
        groups = split_list(c.get("supporting_independence_groups"))
        if c.get("status") in ("corroborated", "confirmed") and len(groups) < 2:
            warn.append(f"claims: {c['claim_id']} помечено «{c['status']}», но независимых групп {len(groups)} (<2)")
    for r in case.data["rel"]:
        if r.get("status") in strong and not ids_in(r.get("supporting_artifact_ids"), "A"):
            warn.append(f"rel: {r['relationship_id']} «{r['status']}» без подтверждающих артефактов")
    for s in case.data["search"]:
        if s.get("result_status") == "found" and not ids_in(s.get("artifact_ids"), "A"):
            warn.append(f"search: {s['search_id']} found, но находка не занесена в artifact-register")
    # 4. живые
    for p in case.data["person"]:
        if p.get("living_status") == "living" and re.search(r"\d{4}-\d{2}-\d{2}", p.get("birth_estimate", "")):
            warn.append(f"person: {p['person_id']} живой с полной датой рождения — в публичное не выносить")
    for x in err:
        print("ОШИБКА ", x)
    for x in warn:
        print("внимание", x)
    n = {t: len(case.data[t]) for t in REG}
    print(f"итого: {n['person']} людей, {n['artifact']} находок, {n['claim']} утверждений, "
          f"{n['rel']} связей, {n['search']} запросов · ошибок {len(err)}, предупреждений {len(warn)}")
    sys.exit(1 if err else 0)


# --- дерево из relationship-register ---
MALE_A = {"илья", "никита", "фома", "кузьма", "савва", "лука", "иона", "мина", "foma", "ilya", "nikita"}
FEMALE_X = {"любовь", "нинель", "агарь", "руфь", "эсфирь"}


def sex_guess(label):
    first = norm(label).split(" ")[0] if label else ""
    if first in MALE_A:
        return "F"
    if first in FEMALE_X or first.endswith(("а", "я")):
        return "M"
    return "F"


def parents(case):
    """child_id → {'F': id, 'M': id} по связям parent-of / father-of / mother-of / child-of."""
    pidx = case.index("person")
    out = {}
    for r in case.data["rel"]:
        t = norm(r.get("relationship_type"))
        a_, b_ = r.get("person_a_id"), r.get("person_b_id")
        if r.get("status") in ("rejected", "refuted"):
            continue
        if t in ("parent-of", "father-of", "mother-of"):
            par, ch = a_, b_
        elif t == "child-of":
            par, ch = b_, a_
        else:
            continue
        side = "F" if t == "father-of" else "M" if t == "mother-of" else sex_guess(pidx.get(par, {}).get("preferred_label"))
        out.setdefault(ch, {})[side] = par
    return out


def root_id(case, given):
    if given:
        return given
    for p in case.data["person"]:
        if p.get("candidate_status") in ("confirmed-subject", "subject"):
            return p["person_id"]
    sys.exit("укажите --root P-xxxx (или candidate_status=confirmed-subject у человека в центре)")


def walk(case, root, depth):
    """Путь в веере (F/M от центра) → person_id."""
    par = parents(case)
    paths, todo = {"": root}, [""]
    while todo:
        path = todo.pop()
        if len(path) >= depth:
            continue
        for side, pid in par.get(paths[path], {}).items():
            paths[path + side] = pid
            todo.append(path + side)
    return paths


def cmd_next(a):
    case = Case(a.dir)
    root = root_id(case, a.root)
    pidx = case.index("person")
    paths = walk(case, root, a.depth)
    print(f"Центр: {pidx.get(root, {}).get('preferred_label', root)}. В дереве {len(paths)} чел. до {a.depth} колена.")
    print("\nПустые места (следующий шаг поиска):")
    gaps = 0
    for path in sorted(paths, key=lambda x: (len(x), x)):
        if len(path) + 1 > a.depth:
            continue
        for side, who in (("F", "нет отца"), ("M", "нет матери")):
            if path + side not in paths:
                pid = paths[path]
                print(f"  - {who}: {pidx.get(pid, {}).get('preferred_label', pid)} ({pid})")
                gaps += 1
    if not gaps:
        print("  нет")
    unlinked = [p for p in case.data["person"] if p["person_id"] not in paths.values()
                and p.get("candidate_status") not in ("rejected", "namesake-candidate", "excluded")]
    if unlinked:
        print(f"\nЛюди в реестре без связи с центром: {len(unlinked)} (связать через rod.py add rel или исключить)")
        for p in unlinked[: a.limit]:
            print(f"  - {p['person_id']} {p.get('preferred_label', '')}")
        if len(unlinked) > a.limit:
            print(f"  … и ещё {len(unlinked) - a.limit}")
    st = os.path.join(a.dir, "research-state.md")
    if os.path.exists(st):
        q = [l.strip() for l in open(st, encoding="utf-8") if re.match(r"\s*- \[ \]\s*\S", l)]
        print(f"\nОткрытая очередь в research-state.md: {len(q)}")
        for l in q[: a.limit]:
            print("  " + l)
    blocked = [s for s in case.data["search"] if s.get("result_status", "").startswith("blocked")]
    if blocked:
        print(f"\nЗаблокировано (нужен человек): {len(blocked)}")
        for s in blocked[: a.limit]:
            print(f"  - {s['search_id']} {s.get('source_or_domain')}: {s.get('query')} — {s.get('blocker')}")


def plural(surname):
    s = surname
    for end in ("ова", "ева", "ина", "ына", "ая"):
        if s.endswith(end):
            return s[:-1] + "ы" if end != "ая" else s[:-2] + "ие"
    if s.endswith(("ов", "ев", "ин", "ын")):
        return s + "ы"
    if s.endswith(("ий", "ой")):
        return s[:-2] + "ие"
    return s


SUR_END = ("ов", "ев", "ёв", "ин", "ын", "ова", "ева", "ёва", "ина", "ына", "ский", "ская", "цкий", "цкая")


def split_label(label):
    """«Соколов Иван Петрович (дед по маме)» → ("Иван Петрович", "Соколов").
    Комментарии после « — » и в скобках отбрасываются; одно слово с заглавной в скобках — девичья/вторая фамилия."""
    base = re.split(r"\s+[—–-]\s+", label)[0]
    keep = ""
    for par in re.findall(r"\(([^)]*)\)", base):
        w = re.sub(r"^урожд\.\s*", "", par.strip()).rstrip("?").split("/")[0]
        if re.fullmatch(r"[А-ЯЁ][а-яё]+", w) and w.endswith(SUR_END):  # девичья/вторая фамилия, не прозвище
            keep = w + ("?" if par.strip().endswith("?") else "")
    base = re.sub(r"\([^)]*\)|«[^»]*»", " ", base)
    toks = [t.split("/")[0] for t in base.split() if t]
    if len(toks) >= 2 and toks[0].endswith(SUR_END) and not toks[-1].endswith(SUR_END):
        toks = toks[1:] + toks[:1]  # фамилия стояла первой
    if len(toks) >= 2 and toks[-1].endswith(SUR_END):
        n, s_ = " ".join(toks[:-1]), toks[-1]
    else:
        n, s_ = " ".join(toks), ""
    if keep:
        s_ = f"{s_} ({keep})" if s_ else keep.rstrip("?")
    return n or label, s_


def year(v):
    m = re.search(r"1[5-9]\d\d|20\d\d", v or "")
    return m.group(0) if m else ""


PALETTE = ["#3b6ea5", "#4f8a5b", "#c08a2e", "#a54a5e", "#8a6bb8", "#b5653a", "#7a5a8a", "#2f7f86"]


def cmd_fan(a):
    import fan_build
    case = Case(a.dir)
    root = root_id(case, a.root)
    pidx, aidx = case.index("person"), case.index("artifact")
    paths = walk(case, root, 6)
    claims_by = {}
    for c in case.data["claim"]:
        for pid in ids_in(c.get("subject_person_ids"), "P"):
            claims_by.setdefault(pid, []).append(c)
    people = {}
    for path, pid in paths.items():
        p = pidx.get(pid)
        if not p:
            continue
        n, s = split_label(p.get("preferred_label", pid))
        living = p.get("living_status") == "living"
        cs = p.get("candidate_status", "")
        prov = {aidx.get(x, {}).get("provenance_level", "") for x in ids_in(p.get("source_artifact_ids"), "A")}
        if cs in ("hypothesis", "namesake-candidate", "candidate"):
            c = "h"
        elif prov - {"", "family-memory", "user-statement"}:
            c = "d"
        else:
            c = "f"
        if living and path:  # живые, кроме центра: только имя
            people[path] = dict(n=n, s=s, y="", p="", c="f", t=["Живой человек — в веере только имя."])
            continue
        b, d = year(p.get("birth_estimate")), year(p.get("death_estimate"))
        y = f"{b}–{d}" if b and d else b or (f"† {d}" if d else "")
        t = [x["claim_text"] for x in claims_by.get(pid, []) if x.get("status") not in ("rejected", "refuted")]
        if not t and p.get("notes") and not living:
            t = [p["notes"]]
        people[path] = dict(n=n, s=s, y=y, p=split_list(p.get("places"))[0] if p.get("places") else "", c=c, t=t)
    # линии: новая фамилия в пути — новая линия
    lines, ev = [], []
    for path in sorted(people, key=lambda x: (len(x), x)):
        if not path:
            continue
        sur = people[path]["s"].split(" ")[0]
        child = people.get(path[:-1], {}).get("s", "").split(" ")[0]
        sur = re.sub(r"[^А-ЯЁа-яёA-Za-z-]", "", sur)
        child = re.sub(r"[^А-ЯЁа-яёA-Za-z-]", "", child)
        if sur and (path in ("F", "M") or plural(sur) != plural(child)):
            name = plural(sur) or "?"
            ln = next((l for l in lines if l["name"] == name), None)
            if ln:
                ln["pre"].append(path)
            else:
                lines.append(dict(k=f"l{len(lines)}", name=name, color=PALETTE[len(lines) % len(PALETTE)],
                                  pre=[path], text=""))
    def lane(path):
        best, bl = "ctx", -1
        for l in lines:
            for pre in l["pre"]:
                if path.startswith(pre) and len(pre) > bl:
                    best, bl = l["k"], len(pre)
        return best
    for path, p in people.items():
        pid = paths[path]
        pr = pidx[pid]
        if pr.get("living_status") == "living" and path:
            continue
        who = f"{p['n']} {p['s']}".strip()
        if year(pr.get("birth_estimate")):
            ev.append([int(year(pr["birth_estimate"])), lane(path), p["c"], f"Рождение: {who}."])
        if year(pr.get("death_estimate")):
            ev.append([int(year(pr["death_estimate"])), lane(path), p["c"], f"Смерть: {who}."])
    ev.sort(key=lambda e: e[0])
    meta = dict(title=a.title or "Веер предков",
                footer="Собрано rod.py из реестров дела. Гипотезы — пунктиром. Живые, кроме центра, — только имя.")
    D = dict(meta=meta, people=people, lines=lines, events=ev)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(D, f, ensure_ascii=False, indent=1)
    size, n, ni = fan_build.build(D, a.out, a.img)
    print(f"ok {a.out}: {n} чел., {len(lines)} линий, {len(ev)} событий, {size} байт")


PRIV = [
    ("телефон", re.compile(r"(?<!\d)(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)")),
    ("почта", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("ключ", re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|\d{8,10}:[A-Za-z0-9_-]{30,})")),
    ("дата рождения", re.compile(r"\b\d{2}\.\d{2}\.(?:19[3-9]\d|20[0-2]\d)\b")),
]
SKIP_DIRS = {".git", "node_modules", "__pycache__"}


def cmd_privacy(a):
    words = []
    if a.words:
        words = [w.strip() for w in open(a.words, encoding="utf-8") if w.strip() and not w.startswith("#")]
    wre = re.compile("|".join(re.escape(w) for w in words), re.I) if words else None
    allow = re.compile(a.allow) if a.allow else None
    hits = 0
    files = [a.path] if os.path.isfile(a.path) else []
    if not files:
        for r, ds, fs in os.walk(a.path):
            ds[:] = [d for d in ds if d not in SKIP_DIRS]
            files += [os.path.join(r, f) for f in fs]
    for fp in files:
        try:
            text = open(fp, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            found = [("слово", m.group(0)) for m in wre.finditer(line)] if wre else []
            found += [(n, m.group(0)) for n, rx in PRIV for m in rx.finditer(line)]
            for kind, val in found:
                if allow and allow.search(val):
                    continue
                hits += 1
                print(f"{fp}:{i}: {kind}: {val}")
    print(f"privacy: {len(files)} файлов, находок {hits}" + ("" if words else " (без списка слов — только телефоны/почты/ключи/даты)"))
    sys.exit(1 if hits else 0)


def main():
    ap = argparse.ArgumentParser(prog="rod.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=".", help="папка дела (по умолчанию текущая)")
    sp = ap.add_subparsers(dest="cmd", required=True)

    sp.add_parser("init").set_defaults(fn=cmd_init)

    p = sp.add_parser("log"); p.set_defaults(fn=cmd_log)
    p.add_argument("--query", required=True); p.add_argument("--source", required=True)
    p.add_argument("--status", required=True); p.add_argument("--filters"); p.add_argument("--persons")
    p.add_argument("--goal"); p.add_argument("--artifacts"); p.add_argument("--blocker"); p.add_argument("--next")
    p.add_argument("--date"); p.add_argument("--lang", default="ru")

    p = sp.add_parser("add"); p.set_defaults(fn=cmd_add)
    p.add_argument("type", choices=["person", "artifact", "claim", "rel"])
    p.add_argument("--set", action="append", metavar="поле=значение")
    p.add_argument("--force", action="store_true", help="перезаписать непустые поля")
    p.add_argument("-q", "--quiet", action="store_true", help="печатать только id")

    sp.add_parser("check").set_defaults(fn=cmd_check)

    p = sp.add_parser("next"); p.set_defaults(fn=cmd_next)
    p.add_argument("--root"); p.add_argument("--depth", type=int, default=5); p.add_argument("--limit", type=int, default=15)

    p = sp.add_parser("fan"); p.set_defaults(fn=cmd_fan)
    p.add_argument("out"); p.add_argument("--root"); p.add_argument("--img"); p.add_argument("--title")
    p.add_argument("--json", help="сохранить и промежуточный JSON")

    p = sp.add_parser("privacy"); p.set_defaults(fn=cmd_privacy)
    p.add_argument("path"); p.add_argument("--words", help="файл: по слову/фамилии на строку")
    p.add_argument("--allow", help="регулярка разрешённых значений (напр. noreply@)")

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
