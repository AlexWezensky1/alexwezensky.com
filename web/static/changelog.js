"use strict";

/* The changelog is the git log of the solvers repository. It comes in as a
   flat list of commits, and gets grouped here by month before it is drawn --
   the commit messages themselves are already written to be read this way. */

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

  const groups = new Map();
  for (const entry of data.entries) {
    const key = entry.date.slice(0, 7);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(entry);
  }

  for (const [key, entries] of groups) {
    feed.appendChild(monthSection(key, entries));
  }
  status.hidden = true;
}

function monthSection(key, entries) {
  const section = document.createElement("section");
  section.className = "group";

  const title = document.createElement("h2");
  title.className = "month";
  title.textContent = readableMonth(key);
  section.appendChild(title);

  const list = document.createElement("div");
  list.className = "entries";
  for (const entry of entries) list.appendChild(entryElement(entry));
  section.appendChild(list);
  return section;
}

function readableMonth(key) {
  const [year, month] = key.split("-");
  return MONTH_NAMES[parseInt(month, 10) - 1] + " " + year;
}

function entryElement(entry) {
  const wrap = document.createElement("article");
  wrap.className = "entry";

  const date = document.createElement("div");
  date.className = "entry-date";
  const [, month, day] = entry.date.split("-");
  date.textContent = MONTH_NAMES[parseInt(month, 10) - 1].slice(0, 3) + " " + day;
  wrap.appendChild(date);

  const body = document.createElement("div");
  body.className = "entry-body";

  const head = document.createElement("h3");
  head.textContent = entry.subject;
  body.appendChild(head);

  if (entry.body) {
    const paragraph = document.createElement("p");
    paragraph.textContent = entry.body;
    body.appendChild(paragraph);
  }

  wrap.appendChild(body);
  return wrap;
}

load();
