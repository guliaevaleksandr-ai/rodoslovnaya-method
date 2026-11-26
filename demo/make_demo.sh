#!/bin/sh
# Демо-дело выдуманной семьи Соколовых, собранное ТОЛЬКО командами rod.py.
# Можно запускать сколько угодно раз: второй прогон ничего не меняет.
set -e
cd "$(dirname "$0")"
R="python3 ../rod.py --dir case"
$R init

# находки
A1=$($R add artifact -q --set source_title="Рассказ мамы (демо)" --set source_url_or_path="беседа 2026-01-05" \
  --set source_type=user-statement --set provenance_level=family-memory --set independence_group=G-FAM-MOTHER)
A2=$($R add artifact -q --set source_title="Метрическая книга, рождение Фёдора (демо)" \
  --set archival_reference="ГАЭО ф. 000, оп. 2, д. 345, № 17" --set record_date=1901 --set place_as_written=Энск \
  --set provenance_level=archival --set directness=direct --set originality=copy-scan --set independence_group=G-GAEO-345)
A3=$($R add artifact -q --set source_title="Донесение о потерях (демо)" --set archival_reference="ЦАМО ф. 00, оп. 00000, д. 000" \
  --set record_date=1943 --set provenance_level=archival --set independence_group=G-CAMO-000)

# люди
ME=$($R add person -q --set preferred_label="Иван Петрович Соколов" --set birth_estimate=1990 --set living_status=living \
  --set candidate_status=confirmed-subject --set places=Энск --set source_artifact_ids=$A1)
PA=$($R add person -q --set preferred_label="Пётр Николаевич Соколов" --set living_status=living --set source_artifact_ids=$A1)
MA=$($R add person -q --set preferred_label="Ольга Сергеевна Лебедева (Соколова)" --set living_status=living --set source_artifact_ids=$A1)
GF=$($R add person -q --set preferred_label="Николай Фёдорович Соколов" --set birth_estimate=1928 --set death_estimate=1999 \
  --set living_status=deceased --set places=Энск --set source_artifact_ids=$A1)
GGF=$($R add person -q --set preferred_label="Фёдор Ильич Соколов" --set birth_estimate=1901 --set death_estimate=1960 \
  --set living_status=deceased --set places=Энск --set source_artifact_ids=$A2)
MGF=$($R add person -q --set preferred_label="Сергей Андреевич Лебедев" --set birth_estimate=1940 --set death_estimate=2010 \
  --set living_status=deceased --set places="хутор Ольховый" --set source_artifact_ids=$A1)
MGGF=$($R add person -q --set preferred_label="Андрей Кузьмич Лебедев" --set birth_estimate=1912 --set death_estimate=1943 \
  --set living_status=deceased --set places="хутор Ольховый" --set source_artifact_ids=$A3)
MGM=$($R add person -q --set preferred_label="Валентина Павловна Морозова" --set living_status=deceased --set source_artifact_ids=$A1)
HYP=$($R add person -q --set preferred_label="Пётр Соколов" --set death_estimate=1899 --set candidate_status=hypothesis \
  --set notes="Совпадает только имя и фамилия — нужен второй признак")
ILYA=$($R add person -q --set preferred_label="Илья Петрович Соколов" --set living_status=deceased --set source_artifact_ids=$A2)

# утверждения
$R add claim -q --set claim_text="Фёдор Ильич Соколов родился в 1901 году в Энске, отец — Илья Петров" \
  --set subject_person_ids="$GGF;$ILYA" --set claim_type=birth --set artifact_ids=$A2 \
  --set supporting_independence_groups=G-GAEO-345 --set status=single-source >/dev/null
$R add claim -q --set claim_text="Андрей Кузьмич Лебедев погиб в 1943 году" --set subject_person_ids=$MGGF \
  --set claim_type=death --set artifact_ids=$A3 --set supporting_independence_groups=G-CAMO-000 --set status=single-source >/dev/null

# связи (father-of / mother-of — сторона в веере без угадывания)
rel() { $R add rel -q --set person_a_id=$1 --set relationship_type=$2 --set person_b_id=$3 --set status=${4:-single-source} >/dev/null; }
rel $PA father-of $ME
rel $MA mother-of $ME
rel $GF father-of $PA
rel $GGF father-of $GF
rel $ILYA father-of $GGF
rel $HYP father-of $ILYA hypothesis
rel $MGF father-of $MA
rel $MGM mother-of $MA
rel $MGGF father-of $MGF

# журнал поиска
$R log --query "Лебедев Андрей Кузьмич" --source obd-memorial.ru --status found --persons $MGGF --artifacts $A3 >/dev/null
$R log --query "Ветров Иван" --source pamyat-naroda.ru --status blocked-captcha --blocker "капча — человек" >/dev/null

$R check
