-- 业界新闻和深度文章：每天一份（data 是 JSON：快讯、深度、各来源情况）
create table if not exists news (
  date text primary key,
  data text not null
);
