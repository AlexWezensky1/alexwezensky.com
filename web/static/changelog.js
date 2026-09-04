"use strict";

/* The changelog is the git log of the solvers repository. Every commit subject
   is already written as one sentence saying what changed, so the subject is the
   whole entry here. They arrive as a flat list and are grouped by month, and
   then by day, so a day states its date once however much landed on it. */

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

async function load() {
  const feed = document.getElementById("feed");
  const status = document.getElementById("status");
  let data;
  try {
    const response = await fetch("changelog.json", { cache: "no-store" });
    if (!response.ok) throw new Error(response.status);
    data = await response.json();
  } catch (err) {
    status.textContent = "Could not load the changelog.";
    return;
  }

  // month -> day -> everything summarised that day, newest first throughout
  const months = new Map();
  for (const entry of data.entries) {
    const month = entry.date.slice(0, 7);
    if (!months.has(month)) months.set(month, new Map());
    const days = months.get(month);
    if (!days.has(entry.date)) days.set(entry.date, []);
    days.get(entry.date).push(entry);
  }

  for (const [month, days] of months) feed.appendChild(monthSection(month, days));
  status.hidden = true;
}

function monthSection(key, days) {
  const section = document.createElement("section");
  section.className = "group";

  const title = document.createElement("h2");
  title.className = "month";
  title.textContent = readableMonth(key);
  section.appendChild(title);

  const list = document.createElement("div");
  list.className = "days";
  for (const [date, entries] of days) list.appendChild(daySection(date, entries));
  section.appendChild(list);
  return section;
}

function readableMonth(key) {
  const [year, month] = key.split("-");
  return MONTH_NAMES[parseInt(month, 10) - 1] + " " + year;
}

// One day's work: the date said once, its summaries listed beside it.
function daySection(date, entries) {
  const wrap = document.createElement("section");
  wrap.className = "day";

  const label = document.createElement("h3");
  label.className = "day-date";
  const [, month, day] = date.split("-");
  label.textContent = MONTH_NAMES[parseInt(month, 10) - 1].slice(0, 3) + " " + day;
  wrap.appendChild(label);

  const list = document.createElement("ul");
  list.className = "summaries";
  for (const entry of entries) {
    const item = document.createElement("li");
    item.textContent = entry.subject;
    list.appendChild(item);
  }
  wrap.appendChild(list);
  return wrap;
}

load();
