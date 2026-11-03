// Compensation display derived from stated USD figures; conversions assume 40 hours/week, 52 weeks/year.
const unitPattern = '(hourly|hours?|hr|h|weekly|weeks?|wk|monthly|months?|mo|annually|annual(?:ized)?|years?|yr)';
const money = '(?:\\$|USD\\s*)?\\d[\\d,]*(?:\\.\\d+)?\\s*[kK]?';
export function normalizePay(text) {
  if (typeof text !== 'string' || /€|£|¥|\b(?:CAD|EUR|GBP|INR|JPY|CHF|RMB|AUD|NZD|SGD|HKD)\b/i.test(text)) return null;
  const stated = text.replace(/(\d[\d,]*(?:\.\d+)?\s*k?)\s+USD\b/gi,(_,amount)=>'USD '+amount).replace(/\$USD\s*/gi,'USD ');
  const rx = new RegExp('(?:\\$|USD\\s*)(' + money + ')(?:\\s*(?:-|–|—|to)\\s*(' + money + '))?\\s*(?:USD\\s*)?(?:/\\s*|per\\s+|an?\\s+)?\\s*(?:' + unitPattern + '\\b)?', 'gi');
  const figures = [];
  const number = s => Number(s.replace(/[^\d.]/g, '')) * (/k/i.test(s) ? 1000 : 1);
  for (const match of stated.matchAll(rx)) {
    let low = number(match[1]), high = match[2] ? number(match[2]) : low;
    if (match[2] && /k/i.test(match[2]) && !/k/i.test(match[1]) && low < 1000) low *= 1000;
    const word = (match[3] || '').toLowerCase();
    const unit = /^h/.test(word) ? 'hour' : /^w/.test(word) ? 'week' : /^m/.test(word) ? 'month' : word ? 'year'
      : /\bhourly\b/i.test(text) ? 'hour' : /\bweekly\b/i.test(text) ? 'week' : /\bmonthly\b/i.test(text) ? 'month'
      : /annual|salary/i.test(text) && low >= 10000 ? 'year' : null;
    if (unit) figures.push({low, high, unit});
  }
  if (!figures.length) return null;
  const unit = figures[0].unit, same = figures.filter(f => f.unit === unit);
  const low = Math.min(...same.map(f=>f.low)), high = Math.max(...same.map(f=>f.high));
  const limits = {hour:[5,500], week:[200,20000], month:[500,60000], year:[10000,3000000]};
  if (low < limits[unit][0] || high > limits[unit][1]) return null;
  const annual = {hour:2080,week:52,month:12,year:1}[unit];
  return {hourly:[Math.round(low*annual/2080),Math.round(high*annual/2080)],
    yearly:[Math.round(low*annual/1000)*1000,Math.round(high*annual/1000)*1000], unit, currency:'USD',
    basis:['hour','year'].includes(unit) ? 'stated' : 'converted'};
}

export function extractPay(description) {
  if (typeof description !== 'string') return null;
  const lines = description.split(/\n|(?<=[.!?])\s+/).filter(line => /\$|€|£|¥|\b(?:USD|EUR|GBP|CAD|INR|JPY|AUD)\b/i.test(line) && /salary|compensation|pay|hour|\/hr|\/yr|per year|annual|monthly|per month|weekly/i.test(line));
  return lines.length ? lines.join(' ').trim() : null;
}

export function payFields(posting) {
  const pay = posting.pay || extractPay(posting.description);
  return {pay:pay || null, pay_normalized:normalizePay(pay)};
}
