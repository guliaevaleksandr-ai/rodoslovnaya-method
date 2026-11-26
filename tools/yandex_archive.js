// Хелперы для Яндекс «Поиска по архивам». Выполнять в консоли/через javascript_tool
// на открытой вкладке https://yandex.ru/archive/... (после входа в аккаунт).
// Сервер отдаёт результаты в HTML, поэтому хватает fetch + DOMParser.

// Поиск: текст + необязательный диапазон лет. Возвращает число результатов и сниппеты.
window.__qd = async (text, yearFrom = '', yearTo = '', maxChars = 3000) => {
  const url = '/archive/search?text=' + encodeURIComponent(text)
    + (yearFrom ? '&dateFrom=' + yearFrom + '&dateTo=' + yearTo : '');
  const html = await (await fetch(url)).text();
  const doc = new DOMParser().parseFromString(html, 'text/html');
  let s = doc.querySelector('main')?.innerText || '';
  const total = (s.match(/Всего найдено[^\n]*?результат\S*/) || [''])[0];
  s = s.replace(/^[\s\S]*?По релевантности/, '').replace(/Фильтры[\s\S]*/, '');
  return `### ${text} [${yearFrom}-${yearTo}] | ${total}\n${s.slice(0, maxChars)}`;
};

// Ссылки на сканы из выдачи (первая — самая релевантная).
window.__links = async (text) => {
  const html = await (await fetch('/archive/search?text=' + encodeURIComponent(text))).text();
  const doc = new DOMParser().parseFromString(html, 'text/html');
  return [...doc.querySelectorAll('main a')].map(a => a.getAttribute('href'))
    .filter(h => h && h.includes('/archive/catalog/'));
};

// Какие дела (книги) про место есть за конкретный год — по заголовкам.
window.__titles = async (place, year) => {
  const html = await (await fetch('/archive/search?text=' + encodeURIComponent(place)
    + '&dateFrom=' + year + '&dateTo=' + year)).text();
  const s = new DOMParser().parseFromString(html, 'text/html').querySelector('main')?.innerText || '';
  const t = (s.match(/(Метрическ|Книга записей)[^\n]*?(?=1 января|\d{1,2} [а-я]+ 1\d{3})/g) || [])
    .map(x => x.slice(0, 170)).filter(x => x.includes(place.slice(0, 6)));
  return year + ': ' + [...new Set(t)].join(' || ');
};

// Пример пакета:
// (await Promise.all([['Соколов Энск'], ['хутора Ольхового'], ['Ветров Заречье', '1894', '1912']]
//   .map(x => window.__qd(...x)))).join('\n\n')
// (await Promise.all(['1911','1912','1913','1914'].map(y => window.__titles('Заречье', y)))).join('\n')

// Расшифровка страницы (структура) и перебор всей книги с регулярным выражением.
window.__struct = async (u) => {
  const h = await (await fetch(u + '?tab=structured')).text();
  const t = new DOMParser().parseFromString(h, 'text/html').querySelector('main')?.innerText || '';
  const i = t.indexOf('Запись 1');
  return t.slice(i > 0 ? i : 0);
};
window.__scanBook = async (uuid, nScans, re) => {
  const out = [];
  for (let i = 1; i <= nScans; i += 5) {
    await Promise.all([...Array(5).keys()].map(async d => {
      const k = i + d; if (k > nScans) return;
      const t = await window.__struct('/archive/catalog/' + uuid + '/' + k);
      const rx = new RegExp(re, 'g'); let m;
      while ((m = rx.exec(t))) out.push(k + ': …' + t.slice(Math.max(0, m.index - 160), m.index + 220).replace(/\s+/g, ' '));
    }));
  }
  return out;
};
// Пример: await window.__scanBook('<uuid книги из ссылки на каталог>', 85, 'Соколов|Ильи Петров')  // перебрать 85 сканов книги
