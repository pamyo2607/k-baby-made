const SHEET_ID = "1eXWn2qhdL2iX6nDi60Uov7sotgkoM0veieE2CTdBT8I";
const LIVE_CSV = "./data/master-db-419-final.csv";
const CATS = ["완구", "구강·치발기", "턱받이", "수유용품", "이유식·식기", "위생·기저귀"];
const CAT = {
  "완구": ["🧸", "1~200위 최신 조사 완료"],
  "구강·치발기": ["🦷", "Safety Korea와 공식몰 재검증 진행"],
  "턱받이": ["👶", "유아용 섬유제품 안전기준 재검증 진행"],
  "수유용품": ["🍼", "제품별 적용 안전제도 재검증 진행"],
  "이유식·식기": ["🥣", "식품 접촉 안전기준 재검증 진행"],
  "위생·기저귀": ["🫧", "위생용품 법정 기준 재검증 진행"]
};
const S = { products: [], history: [], meta: {}, visible: 24, babyMonths: null, source: "backup" };
const $ = id => document.getElementById(id);
const esc = (value = "") => String(value).replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char]));
const norm = value => String(value || "").normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
const uniq = items => [...new Set(items.filter(Boolean))].sort((a, b) => String(a).localeCompare(String(b), "ko"));
const fmt = number => Number(number || 0).toLocaleString("ko-KR");
const STATUS_LABELS = { "포함": "완료", "보류": "ing", "제외": "❗ 기준 제외·중복" };
const displayStatus = statusValue => STATUS_LABELS[statusValue] || "ing";
const exactSafetyKoreaDetailUrl = (...values) => values
  .flat()
  .map(value => String(value || "").trim())
  .find(value => /^https?:\/\/(?:www\.)?safetykorea\.kr\/release\/certDetail(?:[?#]|$)/i.test(value)) || "";
const normalizeKcNumber = value => {
  const number = String(value || "").trim();
  return /^[A-Z]{1,3}\d[A-Z0-9-]{5,}[A-Z0-9]$/i.test(number) ? number.toUpperCase() : "";
};
const matchingSafetyKoreaDetailUrl = (certNumber, ...values) => {
  const expected = normalizeKcNumber(certNumber);
  if (!expected) return "";
  return values.flat(Infinity).map(value => String(value || "").trim()).find(value => {
    const detail = exactSafetyKoreaDetailUrl(value);
    if (!detail) return false;
    try {
      return normalizeKcNumber(new URL(detail).searchParams.get("certNum")) === expected;
    } catch {
      return false;
    }
  }) || "";
};
const safetyKoreaDetailLink = (...values) => {
  const detailUrl = exactSafetyKoreaDetailUrl(...values);
  return detailUrl
    ? `<a class="safetykorea-link" href="${esc(detailUrl)}" target="_blank" rel="noopener">Safety Korea 공식 상세 열기 ↗</a>`
    : '<span class="safetykorea-link safetykorea-unavailable" aria-disabled="true">정확한 Safety Korea 상세 URL 확인 중</span>';
};
function safetyInfo(product) {
  const info = certificationInfo(product);
  const numbers = info.found.map(item => item.certNumber).filter(Boolean);
  return {
    regime: product.regulatoryRegime || "어린이제품안전특별법 적용 여부와 Safety Korea 동일 모델 확인",
    detail: numbers.length ? `Safety Korea 상세 확인 인증번호 ${numbers.join(" · ")}` : "동일 제품 Safety Korea 인증번호 자동 재검증 중"
  };
}


function formatCertDate(value) {
  const text = String(value || "").replace(/[^0-9]/g, "");
  return text.length === 8 ? `${text.slice(0, 4)}.${text.slice(4, 6)}.${text.slice(6, 8)}` : (value || "확인 중");
}
function certificationInfo(product) {
  const certifications = Array.isArray(product.certifications) ? product.certifications : [];
  const found = certifications.filter(item => item?.found === true && matchingSafetyKoreaDetailUrl(
    item.certNumber,
    item.url,
    product.safetyKoreaSearchUrl,
    product.officialUrls
  ));
  const statuses = uniq(found.map(item => item.status || "확인 필요"));
  const primaryStatus = statuses.includes("적합") ? "적합" : statuses.includes("기간만료") ? "기간만료" : statuses[0] || (normalizeKcNumber(product.kcNumber) ? "상세 조회 중" : "인증번호 확인 중");
  return {
    certifications,
    found,
    primaryStatus,
    statusClass: primaryStatus === "적합" ? "cert-active" : primaryStatus === "기간만료" ? "cert-expired" : "cert-checking",
    certDate: product.certDateSummary || found.map(item => item.certDate).filter(Boolean).map(formatCertDate).join(" · ") || "확인 중",
    certType: product.certTypeSummary || uniq(found.map(item => item.certType)).join(" · ") || "확인 중",
    authority: product.certAuthoritySummary || uniq(found.map(item => item.authority)).join(" · ") || "확인 중"
  };
}
function certificationCards(product) {
  const info = certificationInfo(product);
  if (!info.certifications.length) return `<div class="cert-empty"><strong>Safety Korea 상세 확인 중</strong><p>${esc(product.kcNumber || "인증번호 미확인")}</p>${safetyKoreaDetailLink(product.safetyKoreaSearchUrl, product.officialUrls)}</div>`;
  return info.certifications.map(cert => {
    const related = (cert.relatedCertificates || []).map(item => `<li><span>${esc(item.certNumber)}</span><strong class="${item.status === "적합" ? "text-active" : item.status === "기간만료" ? "text-expired" : ""}">${esc(item.status)}</strong></li>`).join("");
    const row = (label, value) => `<div><span>${label}</span><strong>${esc(value || "확인 중")}</strong></div>`;
    return `<article class="cert-detail-card"><div class="cert-detail-head"><div><span>인증번호</span><strong>${esc(cert.certNumber)}</strong></div><b class="cert-status ${cert.status === "적합" ? "cert-active" : cert.status === "기간만료" ? "cert-expired" : "cert-checking"}">${esc(cert.status || "확인 필요")}</b></div><div class="detail-grid cert-grid">${row("인증일자", formatCertDate(cert.certDate))}${row("인증변경일자", formatCertDate(cert.changedDate))}${row("인증구분", cert.certType)}${row("인증기관", cert.authority)}${row("인증변경사유", cert.changedReason)}${row("리콜현황", cert.recallStatus || "해당 없음")}${row("품목명", cert.itemName)}${row("모델명", cert.modelName)}${row("제조사", cert.manufacturer)}${row("제조국", cert.country)}${row("수입업체", cert.importer)}${row("제품분류", cert.classification)}</div>${related ? `<div class="related-certificates"><strong>연관 인증번호</strong><ul>${related}</ul></div>` : ""}${safetyKoreaDetailLink(cert.url, product.safetyKoreaSearchUrl, product.officialUrls)}</article>`;
  }).join("");
}

function normalizeCategory(value) {
  const text = String(value || "").replace(/[\s·ㆍ/]+/g, "").toLowerCase();
  if (text.includes("치발") || text.includes("구강")) return "구강·치발기";
  if (text.includes("턱받")) return "턱받이";
  if (text.includes("수유")) return "수유용품";
  if (text.includes("이유식") || text.includes("식기")) return "이유식·식기";
  if (text.includes("위생") || text.includes("기저귀") || text.includes("물티슈")) return "위생·기저귀";
  if (text.includes("완구") || text.includes("장난감")) return "완구";
  return String(value || "기타").trim() || "기타";
}

function csv(text) {
  const output = [];
  let row = [], cell = "", quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (quoted) {
      if (char === '"' && text[index + 1] === '"') { cell += '"'; index += 1; }
      else if (char === '"') quoted = false;
      else cell += char;
    } else if (char === '"') quoted = true;
    else if (char === ",") { row.push(cell); cell = ""; }
    else if (char === "\n") { row.push(cell.replace(/\r$/, "")); output.push(row); row = []; cell = ""; }
    else cell += char;
  }
  if (cell || row.length) { row.push(cell); output.push(row); }
  return output;
}

function status(value) {
  if (value === "공개") return "포함";
  return ["포함", "보류", "제외"].includes(value) ? value : "보류";
}

function liveProducts(rows) {
  if (rows.length < 2) return [];
  const header = rows[0];
  const indexOf = (...names) => names.map(name => header.indexOf(name)).find(index => index >= 0) ?? -1;
  const get = (row, index) => index < 0 ? "" : String(row[index] || "").trim();
  const I = {
    id: indexOf("ID"), cat: indexOf("제품군"), sub: indexOf("세부 유형"), brand: indexOf("브랜드"), name: indexOf("정확한 제품명"),
    status: indexOf("현재 판정"), country: indexOf("완제품 제조국"), sale: indexOf("국내 판매 상태"), age: indexOf("대상 월령"),
    ageEvidence: indexOf("월령 근거"), kcApplicable: indexOf("KC 대상 여부"), kcNumber: indexOf("KC 인증번호"), platform: indexOf("확인 플랫폼"),
    checkedAt: indexOf("확인일"), reason: indexOf("판정·검증 사유"), url: indexOf("출처 URL"), quality: indexOf("데이터 품질"),
    history: indexOf("누적 이력"), group: indexOf("대시보드 그룹"),
    duplicateOf: indexOf("duplicateOf"), canonicalProductId: indexOf("canonicalProductId")
  };
  return rows.slice(1).filter(row => get(row, I.id) && get(row, I.name)).map(row => {
    const historySummary = get(row, I.history);
    const reason = get(row, I.reason);
    const duplicateMatch = (historySummary + reason).match(/(TOY-\d{8}-\d{3}).*통합/);
    const urls = get(row, I.url).split(/\n+/).filter(Boolean);
    return {
      id: get(row, I.id), category: normalizeCategory(get(row, I.cat) || "기타"), subtype: get(row, I.sub) || get(row, I.group) || "미확인",
      brand: get(row, I.brand) || "미확인", name: get(row, I.name), manufacturer: "미확인",
      status: status(get(row, I.status)), countryOfManufacture: (get(row, I.country) || "미확인").replace("❓", "").trim(),
      saleStatus: get(row, I.sale) || "미확인", ageRange: get(row, I.age) || "확인 중", ageEvidence: get(row, I.ageEvidence) || "공식 근거 부족",
      kcApplicable: get(row, I.kcApplicable) || "확인 필요", kcType: "미확인", kcNumber: get(row, I.kcNumber) || "미확인",
      testInstitute: "미확인", platform: get(row, I.platform) || "미확인", checkedAt: get(row, I.checkedAt), reason: reason || "판정 사유 확인 중",
      officialUrls: urls, saleUrls: urls, quality: get(row, I.quality) || "확인 중", historySummary,
      dashboardGroup: get(row, I.group) || get(row, I.sub),
      duplicateOf: get(row, I.duplicateOf) || (duplicateMatch ? duplicateMatch[1] : ""),
      canonicalProductId: get(row, I.canonicalProductId) || get(row, I.duplicateOf) || get(row, I.id),
      source: "현재 Google Sheet Master DB", sourcePriority: 1, archive: false, aliases: []
    };
  });
}

async function decode(payload) {
  if (payload?.encoding !== "gzip-base64") return payload;
  if (typeof DecompressionStream === "undefined") throw new Error("이 브라우저는 제품 데이터 압축 해제를 지원하지 않습니다.");
  const bytes = Uint8Array.from(atob(payload.data), char => char.charCodeAt(0));
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  return JSON.parse(await new Response(stream).text());
}

function expand(payload) {
  if (Array.isArray(payload)) return payload;
  const defaults = payload.defaults || {};
  const unpack = (schema, rows) => (rows || []).map(row => {
    const product = { ...defaults };
    schema.forEach((key, index) => { product[key] = row[index]; });
    return product;
  });
  if (payload.schema) return unpack(payload.schema, payload.rows);
  return [...unpack(payload.currentSchema, payload.currentRows), ...unpack(payload.historicalSchema, payload.historicalRows)];
}

function merge(fallback, live) {
  const products = new Map(fallback.map(product => [product.id, product]));
  live.forEach(product => {
    const previous = products.get(product.id);
    const canonicalDuplicateOf = previous ? (previous.duplicateOf || "") : (product.duplicateOf || "");
    const canonicalRevalidation = Boolean(previous && (
      Object.prototype.hasOwnProperty.call(previous, "revalidationMissingFields") ||
      Array.isArray(previous.revalidationQueries) ||
      previous.ocrFallbackAttempted !== undefined
    ));
    products.set(product.id, {
      ...(previous || {}),
      ...product,
      id: previous?.id || product.id,
      status: canonicalRevalidation ? previous.status : product.status,
      reason: canonicalRevalidation ? previous.reason : product.reason,
      checkedAt: canonicalRevalidation ? previous.checkedAt : product.checkedAt,
      quality: canonicalRevalidation ? previous.quality : product.quality,
      countryOfManufacture: canonicalRevalidation && previous.countryOfManufacture ? previous.countryOfManufacture : product.countryOfManufacture,
      ageRange: canonicalRevalidation && previous.ageRange ? previous.ageRange : product.ageRange,
      ageEvidence: canonicalRevalidation && previous.ageEvidence ? previous.ageEvidence : product.ageEvidence,
      regulatoryRegime: previous?.regulatoryRegime || product.regulatoryRegime,
      certifications: previous?.certifications?.length ? previous.certifications : product.certifications,
      saleStatus: product.saleStatus || previous?.saleStatus,
      saleUrls: uniq([...(previous?.saleUrls || []), ...(product.saleUrls || [])]),
      officialUrls: uniq([...(previous?.officialUrls || []), ...(product.officialUrls || [])]),
      duplicateOf: canonicalDuplicateOf,
      aliases: uniq([...(previous?.aliases || []), ...(product.aliases || [])])
    });
  });
  return [...products.values()];
}

function bounds(text) {
  const value = String(text || "");
  if (/확인 중|미확인/.test(value)) return null;
  const years = value.match(/(\d+)\s*세\s*이상/);
  if (years) return { min: Number(years[1]) * 12, max: 999 };
  const months = [...value.matchAll(/(\d+)\s*개월/g)].map(match => Number(match[1]));
  if (months.length > 1) return { min: months[0], max: months[1] };
  if (months.length === 1) return /이상/.test(value) ? { min: months[0], max: 999 } : { min: 0, max: months[0] };
  if (/0세 이상/.test(value)) return { min: 0, max: 999 };
  return null;
}

const ageFit = (product, months) => { const range = bounds(product.ageRange); return range ? months >= range.min && months <= range.max : null; };
const SALE_STATUS_BLOCKED = /재검증|확인\s*(?:필요|중)|미확인|품절|종료|단종|직구|구매\s*불가/;
const SALE_STATUS_CONFIRMED = /판매.*확인|구매.*(?:링크|가능|확인)/;
const current = product => {
  const saleStatus = String(product.saleStatus || "");
  return !product.archive
    && product.status !== "제외"
    && !product.duplicateOf
    && !SALE_STATUS_BLOCKED.test(saleStatus)
    && SALE_STATUS_CONFIRMED.test(saleStatus);
};
const evidence = product => (product.kcNumber && product.kcNumber !== "미확인" ? 3 : 0) + (product.officialUrls?.length ? 2 : 0) + (product.countryOfManufacture && !/미확인/.test(product.countryOfManufacture) ? 1 : 0) + (product.ageEvidence && !/부족|재검증/.test(product.ageEvidence) ? 1 : 0);
const blob = product => norm([product.id, product.name, product.brand, product.manufacturer, product.officialModel, product.officialSku, product.kcNumber, product.certStatusSummary, product.certTypeSummary, product.certAuthoritySummary, product.countryOfManufacture, product.subtype, ...(product.aliases || [])].join(" "));

function filters() {
  return {
    q: norm($("searchInput").value), cat: $("categoryFilter").value, st: $("statusFilter").value, sub: $("subtypeFilter").value,
    age: $("ageFilter").value, country: $("countryFilter").value, kc: $("kcFilter").value, sale: $("saleFilter").value,
    quality: $("qualityFilter").value, sort: $("sortFilter").value, excluded: $("showExcluded").checked
  };
}

function list() {
  const filter = filters();
  const directSearch = filter.q.length > 1;
  const products = S.products.filter(product => {
    const showExcluded = filter.excluded || filter.st === "제외";
    if (product.duplicateOf && !directSearch && !showExcluded) return false;
    if (product.status === "제외" && !directSearch && !showExcluded) return false;
    if (filter.st && product.status !== filter.st) return false;
    if (filter.cat && product.category !== filter.cat) return false;
    if (filter.sub && product.subtype !== filter.sub) return false;
    if (filter.country && product.countryOfManufacture !== filter.country) return false;
    if (filter.q && !filter.q.split(" ").every(term => blob(product).includes(term))) return false;
    if (filter.sale === "current" && !current(product)) return false;
    if (filter.sale === "archive" && !product.archive) return false;
    if (filter.kc === "verified" && !certificationInfo(product).found.length) return false;
    if (filter.kc === "active" && !certificationInfo(product).found.some(item => item.status === "적합")) return false;
    if (filter.kc === "expired" && !certificationInfo(product).found.some(item => item.status === "기간만료")) return false;
    if (filter.kc === "checking" && certificationInfo(product).found.length) return false;
    if (filter.quality === "high" && evidence(product) < 4) return false;
    if (filter.quality === "checking" && evidence(product) >= 4) return false;
    if (filter.age === "verified" && !bounds(product.ageRange)) return false;
    if (filter.age === "checking" && bounds(product.ageRange)) return false;
    if ((filter.age === "fit" || $("ageFitToggle").checked) && S.babyMonths !== null && ageFit(product, S.babyMonths) === false) return false;
    return true;
  });
  const order = { 포함: 0, 보류: 1, 제외: 2 };
  products.sort((a, b) => {
    if (filter.sort === "recent") return String(b.checkedAt).localeCompare(String(a.checkedAt));
    if (filter.sort === "included") return order[a.status] - order[b.status];
    if (filter.sort === "evidence") return evidence(b) - evidence(a);
    if (filter.sort === "name") return a.name.localeCompare(b.name, "ko");
    const rankA = Number(a.estimatedRank), rankB = Number(b.estimatedRank);
    return (Number.isFinite(rankA) ? rankA : 999999) - (Number.isFinite(rankB) ? rankB : 999999) || order[a.status] - order[b.status];
  });
  return products;
}

function stats() {
  const meta = S.meta;
  const set = (id, value) => { $(id).textContent = value; };
  set("totalStat", fmt(meta.totalInvestigated || KBABY_EXPECTED_UNIQUE));
  set("saleStat", fmt(meta.currentSale || S.products.filter(current).length));
  set("includedStat", fmt(meta.included || S.products.filter(product => product.status === "포함" && !product.duplicateOf).length));
  set("pendingStat", fmt(meta.pending || S.products.filter(product => product.status === "보류" && !product.duplicateOf).length));
  set("excludedStat", fmt(meta.excludedAndDuplicate ?? S.products.filter(product => product.status === "제외" || product.duplicateOf).length));
  set("categoryStat", "6"); set("updatedStat", String(meta.lastUpdated || "-").slice(5).replace("-", ".")); set("headerDate", `${meta.lastUpdated || "-"} 업데이트`);
}

function roadmap() {
  const categories = S.meta.categories || {};
  $("categoryRoadmap").innerHTML = CATS.map(category => {
    const products = S.products.filter(product => product.category === category && !product.duplicateOf);
    const included = products.filter(product => product.status === "포함").length;
    const pending = products.filter(product => product.status === "보류").length;
    const excluded = products.filter(product => product.status === "제외").length;
    const statButton = (statusLabel, count) => `<button type="button" class="roadmap-stat" data-roadmap-category="${esc(category)}" data-roadmap-status="${statusLabel}" data-roadmap-count="${count}" aria-pressed="false" aria-label="${esc(category)} ${displayStatus(statusLabel)} ${count}개 제품 보기"><span>${displayStatus(statusLabel)}</span><strong>${fmt(count)}</strong></button>`;
    const total = categories[category] ?? products.length;
    const categoryNote = category === "완구" ? CAT[category][1] : `1~${fmt(total)}위 최신 조사 반영 · ${CAT[category][1]}`;
    return `<article class="roadmap-card"><div class="roadmap-head"><strong>${CAT[category][0]} ${category}</strong><b>${fmt(total)}개</b></div><p>${esc(categoryNote)}</p><div class="roadmap-stats">${statButton("포함", included)}${statButton("보류", pending)}${statButton("제외", excluded)}</div></article>`;
  }).join("");
  syncRoadmapSelection();
}

function syncRoadmapSelection() {
  const selectedCategory = $("categoryFilter")?.value || "";
  const selectedStatus = $("statusFilter")?.value || "";
  document.querySelectorAll("[data-roadmap-category][data-roadmap-status]").forEach(button => {
    const active = button.dataset.roadmapCategory === selectedCategory && button.dataset.roadmapStatus === selectedStatus;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function applyRoadmapFilter(category, statusLabel) {
  ["searchInput", "subtypeFilter", "ageFilter", "countryFilter", "kcFilter", "saleFilter", "qualityFilter"].forEach(id => { $(id).value = ""; });
  $("sortFilter").value = "recent";
  $("categoryFilter").value = category;
  $("statusFilter").value = statusLabel;
  $("showExcluded").checked = statusLabel === "제외";
  $("ageFitToggle").checked = false;
  $("filterPanel").classList.add("open");
  render(true);

  const catalog = $("catalog");
  const behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
  requestAnimationFrame(() => {
    catalog.scrollIntoView({ behavior, block: "start" });
    window.setTimeout(() => {
      $("resultCount").setAttribute("tabindex", "-1");
      $("resultCount").focus({ preventScroll: true });
    }, behavior === "smooth" ? 500 : 0);
  });
}


function applySummaryFilter(statusLabel) {
  ["searchInput", "categoryFilter", "subtypeFilter", "ageFilter", "countryFilter", "kcFilter", "saleFilter", "qualityFilter"].forEach(id => { $(id).value = ""; });
  $("sortFilter").value = "recent";
  $("statusFilter").value = statusLabel;
  $("showExcluded").checked = statusLabel === "제외";
  $("ageFitToggle").checked = false;
  $("filterPanel").classList.add("open");
  render(true);
  document.querySelectorAll("[data-summary-status]").forEach(button => button.classList.toggle("is-active", button.dataset.summaryStatus === statusLabel));
  const behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
  requestAnimationFrame(() => $("catalog").scrollIntoView({ behavior, block: "start" }));
}

function card(product) {
  const country = !product.countryOfManufacture || /미확인/.test(product.countryOfManufacture) ? "제조국 확인 필요" : product.countryOfManufacture;
  const safety = safetyInfo(product);
  const cert = certificationInfo(product);
  const reasonTitle = product.status === "제외" ? "기준 제외 사유" : product.status === "보류" ? "추가 확인 사항" : "검증 요약";
  const reason = product.reason || (product.status === "제외" ? "기준 제외 근거 재검증 중" : "공식 근거 확인 중");
  return `<article class="product-card" data-id="${esc(product.id)}"><div class="card-badges"><span class="badge ${product.status === "포함" ? "included" : product.status === "보류" ? "pending" : "excluded"}">${displayStatus(product.status)}</span>${current(product) ? '<span class="badge sale-badge">국내 판매 확인</span>' : product.archive ? '<span class="badge">조사 등록 제품</span>' : ""}</div><h3>${esc(product.name)}</h3><div class="brand-name">${esc(product.brand)}</div><div class="decision-reason ${product.status === "제외" ? "reason-excluded" : product.status === "보류" ? "reason-pending" : "reason-included"}"><span>${reasonTitle}</span><p>${esc(reason)}</p></div><div class="cert-summary"><div><span>KC 인증상태</span><strong class="cert-status ${cert.statusClass}">${esc(cert.primaryStatus)}</strong></div><div><span>인증일자</span><strong>${esc(cert.certDate)}</strong></div><div><span>인증구분</span><strong>${esc(cert.certType)}</strong></div></div><div class="card-meta"><div><span>대상 월령</span><strong>${esc(product.ageRange || "확인 중")}</strong></div><div><span>완제품 제조국</span><strong>${esc(country)}</strong></div><div><span>적용 안전제도</span><strong>${esc(safety.regime)}</strong></div><div><span>확인일</span><strong>${esc(product.checkedAt || "확인 중")}</strong></div></div><div class="card-footer"><span>공식 근거 ${(product.officialUrls || []).length}개</span><button type="button" data-detail="${esc(product.id)}">상세 검증 보기 →</button></div></article>`;
}

function render(resetVisible = false) {
  if (resetVisible) S.visible = 24;
  const products = list(), shown = products.slice(0, S.visible);
  $("loadingSkeleton").hidden = true;
  $("productGrid").innerHTML = shown.map(card).join("");
  $("resultCount").textContent = `${fmt(products.length)}개 표시`;
  $("emptyState").hidden = products.length > 0;
  $("loadMore").hidden = products.length <= S.visible;
  $("productGrid").querySelectorAll("[data-detail]").forEach(button => { button.onclick = () => detail(button.dataset.detail); });
  activeFilters();
  syncRoadmapSelection();
}

function activeFilters() {
  const filter = filters();
  $("activeFilterCount").textContent = [filter.q, filter.cat, filter.st, filter.sub, filter.age, filter.country, filter.kc, filter.sale, filter.quality, filter.excluded].filter(Boolean).length;
}

function updates() {
  const items = S.history.filter(item => item.productId).sort((a, b) => String(b.date).localeCompare(String(a.date))).slice(0, 10);
  $("updatesList").innerHTML = items.map(item => `<article><time>${esc(item.date)}</time><strong>${esc(item.productName || item.productId)}</strong><p>${esc(item.summary || item.reason || "")}</p></article>`).join("");
}

function progress() {
  $("progressList").innerHTML = CATS.map(category => {
    const count = S.products.filter(product => product.category === category && !product.duplicateOf).length;
    const percent = Math.min(100, Math.round(count / 200 * 100));
    return `<article><strong>${CAT[category][0]} ${category}</strong><p>${CAT[category][1]} · 전체 ${fmt(count)}개</p><div class="progress-bar"><span style="width:${percent}%"></span></div></article>`;
  }).join("");
}

function options() {
  $("categoryFilter").innerHTML = '<option value="">전체</option>' + CATS.map(category => `<option>${category}</option>`).join("");
  $("subtypeFilter").innerHTML = '<option value="">전체</option>' + uniq(S.products.map(product => product.subtype)).map(value => `<option>${esc(value)}</option>`).join("");
  $("countryFilter").innerHTML = '<option value="">전체</option>' + uniq(S.products.map(product => product.countryOfManufacture).filter(value => value && !/미확인/.test(value))).map(value => `<option>${esc(value)}</option>`).join("");
}

function detail(id) {
  const product = S.products.find(item => item.id === id);
  if (!product) return;
  const history = S.history.filter(item => item.productId === id).sort((a, b) => String(b.date).localeCompare(String(a.date))).slice(0, 8);
  const urls = uniq([...(product.officialUrls || []), ...(product.saleUrls || [])]);
  const cell = (key, value) => `<div><span>${key}</span><strong>${esc(value || "확인 중")}</strong></div>`;
  const cert = certificationInfo(product);
  $("detailContent").innerHTML = `<div class="detail-body"><div class="card-badges"><span class="badge ${product.status === "포함" ? "included" : product.status === "보류" ? "pending" : "excluded"}">${displayStatus(product.status)}</span><span class="cert-status ${cert.statusClass}">KC ${esc(cert.primaryStatus)}</span></div><h2 class="detail-title">${esc(product.name)}</h2><p>${esc(product.brand)} · ${esc(product.category)} · ${esc(product.subtype)}</p><section class="detail-section"><h3>한눈에 보기</h3><div class="detail-grid">${cell("판정", displayStatus(product.status))}${cell("대상 월령", product.ageRange)}${cell("국내 판매 상태", product.saleStatus)}${cell("완제품 제조국", product.countryOfManufacture)}${cell("최근 확인일", product.checkedAt)}</div></section><section class="detail-section"><h3>Safety Korea 인증 상세</h3><p class="section-note">인증번호별 인증상태와 인증일자와 인증구분과 변경 이력을 Safety Korea 공식 상세에서 확인합니다.</p><div class="certification-list">${certificationCards(product)}</div></section><section class="detail-section"><h3>적용 안전제도와 근거</h3><div class="detail-grid">${cell("적용 안전제도", safetyInfo(product).regime)}${cell("인증·신고 정보", safetyInfo(product).detail)}${cell("시험기관", product.testInstitute)}${cell("KC 대상 여부", product.kcApplicable)}</div><div class="source-links">${urls.map((url, index) => `<a href="${esc(url)}" target="_blank" rel="noopener">근거 자료 ${index + 1} 열기 ↗</a>`).join("") || "공식 URL 보강 중"}</div></section><section class="detail-section"><h3>판정 이유</h3><p>${esc(product.reason)}</p></section><section class="detail-section"><h3>검증 변경 이력</h3>${history.map(item => `<div class="history-item"><strong>${esc(item.date)} · ${esc(displayStatus(item.newStatus || product.status))}</strong><p>${esc(item.summary || item.reason)}</p></div>`).join("") || `<p>${esc(product.historySummary || "변경 이력 보강 중")}</p>`}</section></div>`;
  $("detailDialog").showModal();
}

function age(date) {
  if (!date) return null;
  const birth = new Date(`${date}T00:00:00+09:00`), today = new Date();
  if (birth > today) return null;
  let months = (today.getFullYear() - birth.getFullYear()) * 12 + today.getMonth() - birth.getMonth();
  let anchor = new Date(birth); anchor.setMonth(anchor.getMonth() + months);
  if (anchor > today) { months -= 1; anchor = new Date(birth); anchor.setMonth(anchor.getMonth() + months); }
  return { m: months, d: Math.floor((today - anchor) / 86400000), plus: Math.floor((today - birth) / 86400000) + 1 };
}

function saveAge() {
  const value = $("birthDate").value, result = age(value);
  if (!result) return;
  S.babyMonths = result.m;
  localStorage.setItem("kbabyBirthDate", value); localStorage.setItem("kbabyAgeFit", "true");
  $("babyResult").textContent = `생후 ${result.m}개월 ${result.d}일 · D+${result.plus}`;
  $("ageFitToggle").checked = true;
  render(true);
}

function reset() {
  ["searchInput", "categoryFilter", "statusFilter", "subtypeFilter", "ageFilter", "countryFilter", "kcFilter", "saleFilter", "qualityFilter"].forEach(id => { $(id).value = ""; });
  $("showExcluded").checked = false;
  render(true);
}

function bind() {
  const ids = ["categoryFilter", "subtypeFilter", "ageFilter", "countryFilter", "kcFilter", "saleFilter", "qualityFilter", "sortFilter"];
  document.querySelector(".stats").onclick = event => {
    const button = event.target.closest?.("[data-summary-status]");
    if (!button) return;
    applySummaryFilter(button.dataset.summaryStatus);
  };
  $("categoryRoadmap").onclick = event => {
    const button = event.target.closest?.("[data-roadmap-category][data-roadmap-status]");
    if (!button) return;
    applyRoadmapFilter(button.dataset.roadmapCategory, button.dataset.roadmapStatus);
  };
  $("searchInput").oninput = () => render(true);
  ids.forEach(id => { $(id).onchange = () => render(true); });
  $("statusFilter").onchange = () => {
    $("showExcluded").checked = $("statusFilter").value === "제외";
    render(true);
  };
  $("showExcluded").onchange = () => {
    if ($("statusFilter").value === "제외") $("showExcluded").checked = true;
    render(true);
  };
  $("ageFitToggle").onchange = () => { localStorage.setItem("kbabyAgeFit", String($("ageFitToggle").checked)); render(true); };
  $("resetFilters").onclick = reset;
  $("retryButton").onclick = () => location.reload();
  $("loadMore").onclick = () => { S.visible += 24; render(); };
  $("mobileFilterButton").onclick = () => $("filterPanel").classList.toggle("open");
  $("saveBirth").onclick = saveAge;
  $("removeBirth").onclick = () => {
    localStorage.removeItem("kbabyBirthDate"); localStorage.removeItem("kbabyAgeFit");
    $("birthDate").value = ""; $("ageFitToggle").checked = false; S.babyMonths = null;
    $("babyResult").textContent = "생년월일을 입력하면 현재 월령에 맞는 제품을 우선 표시합니다.";
    render(true);
  };
  $("closeDialog").onclick = () => $("detailDialog").close();
  $("detailDialog").onclick = event => { if (event.target === $("detailDialog")) $("detailDialog").close(); };
}

function source(live, transport = "") {
  const rawCount = S.products.length || KBABY_EXPECTED_RAW;
  const uniqueCount = S.products.filter(product => !product.duplicateOf).length || KBABY_EXPECTED_UNIQUE;
  const sourceLabel = live
    ? (transport === "verified-csv" ? "배포 검증 CSV" : transport === "jsonp" ? "Google Sheet Live DB" : "Live DB")
    : "내장 검증 DB";
  S.source = live ? (transport || "live") : "embedded";
  $("sourceState").innerHTML = `<span></span>${sourceLabel} · 원본 ${fmt(rawCount)}행 · 고유 ${fmt(uniqueCount)}개 · ${live ? "30초 자동 동기화" : "Live DB 재연결 대기"}`;
}

let KBABY_BASE_PRODUCTS = [];
let KBABY_EXPECTED_RAW = 0;
let KBABY_EXPECTED_UNIQUE = 0;
let KBABY_LIVE_SIGNATURE = "";
let KBABY_SYNC_TIMER = null;

function validateLiveSnapshot(products, sourceLabel) {
  if (!Array.isArray(products)) throw new Error(`${sourceLabel} products are invalid`);
  if (KBABY_EXPECTED_RAW && products.length !== KBABY_EXPECTED_RAW) {
    throw new Error(`${sourceLabel} raw count mismatch: ${products.length}/${KBABY_EXPECTED_RAW}`);
  }

  const liveIds = products.map(product => String(product.id || "").trim());
  const liveIdSet = new Set(liveIds);
  if (liveIds.some(id => !id) || liveIdSet.size !== liveIds.length) {
    throw new Error(`${sourceLabel} contains blank or duplicate IDs`);
  }

  const requiredIds = new Set(KBABY_BASE_PRODUCTS.map(product => String(product.id || "").trim()).filter(Boolean));
  const missingIds = [...requiredIds].filter(id => !liveIdSet.has(id));
  const unexpectedIds = [...liveIdSet].filter(id => !requiredIds.has(id));
  if (missingIds.length || unexpectedIds.length) {
    throw new Error(`${sourceLabel} ID set mismatch: missing=${missingIds.slice(0, 5).join("|") || "none"}, unexpected=${unexpectedIds.slice(0, 5).join("|") || "none"}`);
  }

  const expectedDuplicates = new Map(KBABY_BASE_PRODUCTS.filter(product => product.duplicateOf).map(product => [product.id, product.duplicateOf]));
  const liveDuplicates = new Map(products.filter(product => product.duplicateOf).map(product => [product.id, product.duplicateOf]));
  const duplicateMismatch = expectedDuplicates.size !== liveDuplicates.size
    || [...expectedDuplicates].some(([id, canonicalId]) => liveDuplicates.get(id) !== canonicalId);
  if (duplicateMismatch) throw new Error(`${sourceLabel} duplicate map mismatch`);

  const liveUnique = products.length - liveDuplicates.size;
  if (KBABY_EXPECTED_UNIQUE && liveUnique !== KBABY_EXPECTED_UNIQUE) {
    throw new Error(`${sourceLabel} unique count mismatch: ${liveUnique}/${KBABY_EXPECTED_UNIQUE}`);
  }
  return products;
}

function recomputeMeta() {
  const uniqueProducts = S.products.filter(product => !product.duplicateOf);
  const categories = Object.fromEntries(CATS.map(category => [category, uniqueProducts.filter(product => product.category === category).length]));
  const lastUpdated = [S.meta.lastUpdated, ...uniqueProducts.map(product => product.checkedAt)]
    .map(value => String(value || "").match(/\d{4}-\d{2}-\d{2}/)?.[0] || "")
    .filter(Boolean)
    .sort()
    .at(-1) || "";
  S.meta = {
    ...S.meta,
    lastUpdated,
    rawRecords: S.products.length,
    totalInvestigated: uniqueProducts.length,
    currentSale: uniqueProducts.filter(current).length,
    included: uniqueProducts.filter(product => product.status === "포함").length,
    pending: uniqueProducts.filter(product => product.status === "보류").length,
    excluded: uniqueProducts.filter(product => product.status === "제외").length,
    duplicateRecords: S.products.filter(product => product.duplicateOf).length,
    excludedAndDuplicate: S.products.filter(product => product.status === "제외" || product.duplicateOf).length,
    categories
  };
}

function fetchLiveProductsJsonp() {
  return new Promise((resolve, reject) => {
    const callbackName = `__kbabyGviz_${Date.now()}_${Math.random().toString(36).slice(2)}`;
    const script = document.createElement("script");
    let settled = false;
    const cleanup = () => {
      clearTimeout(timer);
      script.remove();
      try { delete window[callbackName]; } catch (_) { window[callbackName] = undefined; }
    };
    const fail = error => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(error instanceof Error ? error : new Error(String(error)));
    };
    const timer = setTimeout(() => fail(new Error("Live DB JSONP timeout")), 15000);
    window[callbackName] = response => {
      if (settled) return;
      try {
        if (!response || response.status !== "ok" || !response.table) {
          throw new Error(`Live DB JSONP status: ${response?.status || "unknown"}`);
        }
        const headers = (response.table.cols || []).map(column => String(column?.label || column?.id || "").trim());
        const rows = (response.table.rows || []).map(row => (row?.c || []).map(cell => {
          if (!cell) return "";
          return String(cell.f ?? cell.v ?? "").trim();
        }));
        const products = validateLiveSnapshot(liveProducts([headers, ...rows]), "Google Sheet Live DB");
        settled = true;
        const signature = JSON.stringify(response.table.rows || []);
        cleanup();
        resolve({ text: signature, products, transport: "jsonp" });
      } catch (error) {
        fail(error);
      }
    };
    script.async = true;
    script.referrerPolicy = "no-referrer";
    const tqx = encodeURIComponent(`out:json;responseHandler:${callbackName}`);
    script.src = `https://docs.google.com/spreadsheets/d/${SHEET_ID}/gviz/tq?sheet=${encodeURIComponent("Master DB")}&headers=1&tqx=${tqx}&_=${Date.now()}`;
    script.onerror = () => fail(new Error("Live DB JSONP script load failed"));
    document.head.appendChild(script);
  });
}

async function fetchLiveProducts() {
  try {
    const separator = LIVE_CSV.includes("?") ? "&" : "?";
    const response = await fetch(`${LIVE_CSV}${separator}_=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`Verified DB CSV ${response.status}`);
    const text = await response.text();
    const products = validateLiveSnapshot(liveProducts(csv(text)), "Verified DB CSV");
    return { text, products, transport: "verified-csv" };
  } catch (verifiedError) {
    console.warn("Verified deployed DB unavailable, trying Google Sheet", verifiedError);
    return await fetchLiveProductsJsonp();
  }
}

async function syncLiveProducts({ initial = false } = {}) {
  try {
    const result = await fetchLiveProducts();
    if (!result.products.length) throw new Error("Live DB contains no products");
    if (!initial && result.text === KBABY_LIVE_SIGNATURE) return false;
    KBABY_LIVE_SIGNATURE = result.text;
    S.products = merge(KBABY_BASE_PRODUCTS, result.products);
    const uniqueCount = S.products.filter(product => !product.duplicateOf).length;
    if (uniqueCount < KBABY_EXPECTED_UNIQUE) throw new Error(`Live DB 병합 결과 부족: ${uniqueCount}/${KBABY_EXPECTED_UNIQUE}`);
    recomputeMeta();
    source(true, result.transport);
    $("headerDate").textContent = "30초 자동 동기화";
    if (!initial) {
      options(); stats(); roadmap(); updates(); progress(); render(true);
    }
    window.__KBABY_VERIFIED_TOTAL = uniqueCount;
    window.__KBABY_LIVE_CONNECTED = true;
    window.__KBABY_LIVE_TRANSPORT = result.transport || "unknown";
    return true;
  } catch (error) {
    console.warn("Live DB sync unavailable", error);
    if (initial) {
      S.products = [...KBABY_BASE_PRODUCTS];
      recomputeMeta();
      source(false);
      $("headerDate").textContent = "Live DB 재시도 중";
      window.__KBABY_VERIFIED_TOTAL = KBABY_EXPECTED_UNIQUE;
      window.__KBABY_LIVE_CONNECTED = false;
      window.__KBABY_LIVE_TRANSPORT = null;
    }
    return false;
  }
}

async function init() {
  $("loadingSkeleton").innerHTML = "<div class='skeleton'></div>".repeat(6);
  try {
    const embedded = window.KBABY_DATA;
    if (!embedded?.fallback) throw new Error("내장 검증 DB가 없습니다.");
    if (!["20260802-full-revalidation2", "20260802-full-revalidation3", "20260804-strict419", "20260804-strict419-fix1", "20260815-live459-recovery1"].includes(embedded.build)) throw new Error(`빌드 불일치: ${embedded.build}`);
    KBABY_EXPECTED_RAW = Number(embedded.validation?.rawRecords || embedded.fallback?.records || 0);
    KBABY_EXPECTED_UNIQUE = Number(embedded.validation?.uniqueProducts || 0);
    if (!Number.isInteger(KBABY_EXPECTED_RAW) || KBABY_EXPECTED_RAW < 1 || !Number.isInteger(KBABY_EXPECTED_UNIQUE) || KBABY_EXPECTED_UNIQUE < 1) throw new Error("내장 검증 메타데이터 불일치");

    const raw = await decode(embedded.fallback);
    const fallback = expand(raw);
    if (fallback.length !== KBABY_EXPECTED_RAW) throw new Error(`전체 원본 행 불일치: ${fallback.length}/${KBABY_EXPECTED_RAW}`);
    fallback.forEach(product => { product.category = normalizeCategory(product.category); });
    const uniqueFallback = fallback.filter(product => !product.duplicateOf);
    if (uniqueFallback.length !== KBABY_EXPECTED_UNIQUE) throw new Error(`고유 제품 불일치: ${uniqueFallback.length}/${KBABY_EXPECTED_UNIQUE}`);

    KBABY_BASE_PRODUCTS = fallback;
    S.products = [...fallback];
    S.history = Array.isArray(embedded.history) ? embedded.history : [];
    S.meta = embedded.meta || {};
    await syncLiveProducts({ initial: true });
    recomputeMeta();

    options(); stats(); roadmap(); updates(); progress(); bind();
    const saved = localStorage.getItem("kbabyBirthDate");
    if (saved) {
      $("birthDate").value = saved;
      const result = age(saved);
      if (result) {
        S.babyMonths = result.m;
        $("babyResult").textContent = `생후 ${result.m}개월 ${result.d}일 · D+${result.plus}`;
      }
    }
    $("ageFitToggle").checked = localStorage.getItem("kbabyAgeFit") === "true" && Boolean(saved);
    $("loadingSkeleton").hidden = true;
    render(true);

    window.__KBABY_DATA_BUILD = embedded.build;
    window.__KBABY_BUILD = document.querySelector('meta[name="kbaby-build"]')?.content || embedded.build;
    window.__KBABY_VERIFIED_TOTAL = S.products.filter(product => !product.duplicateOf).length;
    window.__KBABY_RENDERED_CARDS = $("productGrid").querySelectorAll(".product-card").length;
    window.__KBABY_CATEGORIES = { ...S.meta.categories };
    window.dispatchEvent(new CustomEvent("kbaby:ready", { detail: {
      build: window.__KBABY_BUILD,
      total: window.__KBABY_VERIFIED_TOTAL,
      cards: window.__KBABY_RENDERED_CARDS,
      categories: window.__KBABY_CATEGORIES
    }}));

    if (KBABY_SYNC_TIMER) clearInterval(KBABY_SYNC_TIMER);
    KBABY_SYNC_TIMER = setInterval(() => syncLiveProducts(), 30000);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) syncLiveProducts();
    });
  } catch (error) {
    console.error(error);
    $("loadingSkeleton").hidden = true;
    $("emptyState").hidden = false;
    $("resultCount").textContent = "제품 데이터 검증 실패";
    $("sourceState").innerHTML = `<span></span>제품 데이터 검증 실패 · ${esc(error.message)}`;
    window.__KBABY_BUILD_ERROR = String(error?.stack || error);
  }
}

init();
let kbabyDialogReturnFocus = null;
document.addEventListener("click", event => { const button = event.target.closest?.("[data-detail]"); if (button) kbabyDialogReturnFocus = button; });
document.addEventListener("keydown", event => {
  const dialog = $("detailDialog");
  if (!dialog?.open) return;
  if (event.key === "Escape") { event.preventDefault(); dialog.close(); kbabyDialogReturnFocus?.focus(); return; }
  if (event.key !== "Tab") return;
  const items = [...dialog.querySelectorAll('button,a[href],input,select,textarea,[tabindex]:not([tabindex="-1"])')].filter(item => !item.disabled);
  if (!items.length) return;
  const first = items[0], last = items.at(-1);
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
});
$("detailDialog").addEventListener("close", () => kbabyDialogReturnFocus?.focus());

/* Live DB KC detail bridge. Keeps the existing UI intact while mapping Safety Korea fields from Master DB. */
(() => {
  const legacyLiveProducts = typeof liveProducts === "function" ? liveProducts : null;
  const legacyMerge = typeof merge === "function" ? merge : null;
  if (!legacyLiveProducts || !legacyMerge) {
    console.error("K-Baby Live KC bridge: app.js core is not ready");
    return;
  }

  const clean = value => String(value ?? "").trim();
  const normalizeHeader = value => clean(value).normalize("NFKC").toLowerCase().replace(/[\s_·ㆍ/()\-]+/g, "");
  const splitValues = value => clean(value).split(/\r?\n|\s*[;,|]\s*/).map(clean).filter(Boolean);
  const placeholder = value => !value || /^(미확인|확인\s*중|해당\s*없음|-)$/.test(clean(value));
  const preferLive = (liveValue, fallbackValue) => placeholder(liveValue) ? fallbackValue : liveValue;
  const headerAliases = {
    id: ["ID", "제품 ID", "제품ID"],
    certNumber: ["KC 인증번호", "KC인증번호", "인증번호"],
    certStatus: ["KC 인증상태", "KC인증상태", "인증상태", "KC 상태"],
    certDate: ["인증일자", "KC 인증일자", "KC인증일자"],
    changedDate: ["인증변경일자", "KC 인증변경일자", "KC인증변경일자", "변경일자"],
    certType: ["인증구분", "KC 인증구분", "KC인증구분"],
    authority: ["인증기관", "KC 인증기관", "KC인증기관", "시험기관"],
    modelName: ["KC 모델명", "KC모델명", "인증 모델명", "인증모델명"],
    manufacturer: ["KC 제조사", "제조사", "제조업체", "제조자"],
    importer: ["KC 수입업체", "수입업체", "수입자", "수입사"],
    related: ["연관 인증번호", "연관인증번호", "관련 인증번호", "관련인증번호"],
    changedReason: ["KC 인증변경사유", "인증변경사유", "변경사유"],
    recallStatus: ["리콜현황", "리콜 상태", "리콜상태"],
    itemName: ["KC 품목명", "품목명"],
    classification: ["제품분류", "제품분류코드", "KC 제품분류"],
    country: ["완제품 제조국", "제조국"],
    detailUrl: ["Safety Korea 상세 URL", "Safety Korea URL", "SafetyKorea URL", "KC 상세 URL", "인증 상세 URL"]
  };

  function buildIndex(headers) {
    const normalized = headers.map(normalizeHeader);
    const find = aliases => {
      for (const alias of aliases) {
        const index = normalized.indexOf(normalizeHeader(alias));
        if (index >= 0) return index;
      }
      return -1;
    };
    return Object.fromEntries(Object.entries(headerAliases).map(([key, aliases]) => [key, find(aliases)]));
  }

  function relatedCertificates(value) {
    return splitValues(value).map(entry => {
      const match = entry.match(/^(.+?)(?:\s*[:=()]\s*(적합|기간만료|취소|정지|확인\s*필요)\)?)?$/);
      return {
        certNumber: clean(match?.[1] || entry),
        status: clean(match?.[2] || "확인 필요")
      };
    }).filter(item => item.certNumber);
  }

  function attachLiveCertification(product, row, index) {
    const get = key => index[key] < 0 ? "" : clean(row[index[key]]);
    const certNumbers = splitValues(get("certNumber")).map(normalizeKcNumber).filter(Boolean);
    const status = get("certStatus");
    const certDate = get("certDate");
    const changedDate = get("changedDate");
    const certType = get("certType");
    const authority = get("authority");
    const modelName = get("modelName");
    const manufacturer = get("manufacturer");
    const importer = get("importer");
    const related = relatedCertificates(get("related"));
    const certifications = certNumbers.map(certNumber => ({
      found: true,
      certNumber,
      status: status || "확인 필요",
      certDate,
      changedDate,
      certType,
      authority,
      changedReason: get("changedReason"),
      recallStatus: get("recallStatus"),
      itemName: get("itemName"),
      modelName,
      manufacturer,
      country: get("country") || product.countryOfManufacture,
      importer,
      classification: get("classification"),
      relatedCertificates: related,
      url: matchingSafetyKoreaDetailUrl(certNumber, get("detailUrl"))
    })).filter(certification => certification.url);

    const liveScalars = {
      ...product,
      kcNumber: certNumbers[0] || product.kcNumber,
      kcType: certType || product.kcType,
      testInstitute: authority || product.testInstitute,
      officialModel: modelName || product.officialModel,
      manufacturer: manufacturer || product.manufacturer,
      importer: importer || product.importer,
      certDateSummary: certDate || product.certDateSummary,
      certChangedDateSummary: changedDate || product.certChangedDateSummary,
      certChangedReasonSummary: get("changedReason") || product.certChangedReasonSummary,
      certTypeSummary: certType || product.certTypeSummary,
      certAuthoritySummary: authority || product.certAuthoritySummary
    };

    if (!certifications.length) return liveScalars;

    return {
      ...liveScalars,
      safetyKoreaSearchUrl: certifications[0].url,
      certifications
    };
  }

  liveProducts = function enhancedLiveProducts(rows) {
    const products = legacyLiveProducts(rows);
    if (!Array.isArray(rows) || rows.length < 2 || !products.length) return products;
    const headers = rows[0].map(clean);
    const index = buildIndex(headers);
    const getId = row => index.id < 0 ? "" : clean(row[index.id]);
    const rowsById = new Map(rows.slice(1).map(row => [getId(row), row]).filter(([id]) => id));
    return products.map(product => {
      const row = rowsById.get(clean(product.id));
      return row ? attachLiveCertification(product, row, index) : product;
    });
  };

  merge = function mergeWithLiveCertification(fallback, live) {
    const merged = legacyMerge(fallback, live);
    const liveById = new Map((live || []).map(product => [product.id, product]));
    return merged.map(product => {
      const current = liveById.get(product.id);
      if (!current) return product;
      return {
        ...product,
        kcNumber: preferLive(current.kcNumber, product.kcNumber),
        kcType: preferLive(current.kcType, product.kcType),
        testInstitute: preferLive(current.testInstitute, product.testInstitute),
        officialModel: preferLive(current.officialModel, product.officialModel),
        manufacturer: preferLive(current.manufacturer, product.manufacturer),
        importer: preferLive(current.importer, product.importer),
        certDateSummary: preferLive(current.certDateSummary, product.certDateSummary),
        certChangedDateSummary: preferLive(current.certChangedDateSummary, product.certChangedDateSummary),
        certChangedReasonSummary: preferLive(current.certChangedReasonSummary, product.certChangedReasonSummary),
        certTypeSummary: preferLive(current.certTypeSummary, product.certTypeSummary),
        certAuthoritySummary: preferLive(current.certAuthoritySummary, product.certAuthoritySummary),
        safetyKoreaSearchUrl: preferLive(current.safetyKoreaSearchUrl, product.safetyKoreaSearchUrl),
        certifications: current.certifications?.length ? current.certifications : product.certifications
      };
    });
  };

  certificationCards = function certificationCardsWithLiveFields(product) {
    const info = certificationInfo(product);
    if (!info.certifications.length) {
      return `<div class="cert-empty"><strong>Safety Korea 상세 확인 중</strong><p>${esc(product.kcNumber || "인증번호 미확인")}</p>${safetyKoreaDetailLink(product.safetyKoreaSearchUrl, product.officialUrls)}</div>`;
    }
    return info.certifications.map(cert => {
      const row = (label, value) => `<div><span>${label}</span><strong>${esc(value || "확인 중")}</strong></div>`;
      const related = (cert.relatedCertificates || []).map(item => `<li><span>${esc(item.certNumber)}</span><strong class="${item.status === "적합" ? "text-active" : item.status === "기간만료" ? "text-expired" : ""}">${esc(item.status || "확인 필요")}</strong></li>`).join("");
      return `<article class="cert-detail-card"><div class="cert-detail-head"><div><span>KC 인증번호</span><strong>${esc(cert.certNumber)}</strong></div><b class="cert-status ${cert.status === "적합" ? "cert-active" : cert.status === "기간만료" ? "cert-expired" : "cert-checking"}">${esc(cert.status || "확인 필요")}</b></div><div class="detail-grid cert-grid">${row("KC 인증상태", cert.status)}${row("인증일자", formatCertDate(cert.certDate))}${row("인증변경일자", formatCertDate(cert.changedDate))}${row("인증구분", cert.certType)}${row("인증기관", cert.authority)}${row("KC 모델명", cert.modelName)}${row("제조사", cert.manufacturer)}${row("수입업체", cert.importer)}${row("인증변경사유", cert.changedReason)}${row("리콜현황", cert.recallStatus || "해당 없음")}${row("품목명", cert.itemName)}${row("제조국", cert.country)}${row("제품분류", cert.classification)}</div><div class="related-certificates"><strong>연관 인증번호</strong>${related ? `<ul>${related}</ul>` : "<p>없음 또는 미입력</p>"}</div>${safetyKoreaDetailLink(cert.url, product.safetyKoreaSearchUrl, product.officialUrls)}</article>`;
    }).join("");
  };

  const resync = () => {
    if (typeof syncLiveProducts !== "function") return;
    Promise.resolve(syncLiveProducts({ initial: false })).catch(error => console.warn("Live KC bridge resync failed", error));
  };
  window.addEventListener("kbaby:ready", resync, { once: true });
  window.setTimeout(resync, 0);
  window.__KBABY_LIVE_KC_FIELDS = true;
})();
