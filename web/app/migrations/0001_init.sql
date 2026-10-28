-- The Robot Times 数据库（Cloudflare D1 / SQLite）

-- 推荐过的论文。data 是 JSON：标题、作者、摘要、链接、分数、中文摘要、推荐理由等
create table if not exists papers (
  id text primary key,
  data text not null,
  added_on text not null,
  total real not null default 0
);
create index if not exists papers_added_on on papers(added_on);

-- 每日推荐与阅读计划（data 是 JSON）
create table if not exists days (
  date text primary key,
  data text not null
);

-- 每周回顾与下周计划（data 是 JSON）
create table if not exists weeks (
  week text primary key,
  data text not null
);

-- 我的阅读状态和笔记
create table if not exists notes (
  paper_id text primary key,
  status text not null default 'todo' check (status in ('todo', 'reading', 'done', 'skip')),
  note text not null default '',
  flags text not null default '[]',
  updated_at text not null
);

-- 反馈：喜欢、跳过、加入 Zotero，用来调整之后的推荐
create table if not exists feedback (
  id integer primary key autoincrement,
  paper_id text not null,
  action text not null check (action in ('like', 'skip', 'add')),
  at text not null
);
create index if not exists feedback_paper on feedback(paper_id);

-- 已写入 Zotero 的条目
create table if not exists zotero_items (
  paper_id text primary key,
  item_key text not null,
  at text not null
);

-- 设置（key = 'settings'，value 是 JSON）
create table if not exists config (
  key text primary key,
  value text not null
);
