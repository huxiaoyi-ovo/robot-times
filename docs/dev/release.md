# 发布新版本

用户的看板每天检查一次 GitHub 上的最新发布版本（Release），有新版本就提示，可以一键更新或自动更新。
只有正式发布的版本会推给用户，main 上的日常提交不会。

1. 在 `CHANGELOG.md` 最上面加一段 `## 0.2.0`，写给用户看：改了什么、对他有什么影响。这段会显示在看板的更新提示里。
2. 把 `core/pyproject.toml` 的 `version` 改成 `0.2.0`，运行 `uv lock` 和
   `uv run python -m robot_times.presets > web/app/src/presets.js`（云端版的版本号在这里），测试通过后推到 main。
3. 打标签并推上去：

   ```bash
   git tag v0.2.0 && git push origin v0.2.0
   ```

   `release.yml` 会检查版本号一致、跑测试，然后建好 Release。

版本号规则：修 bug 加最后一位（0.1.1），加功能加中间一位（0.2.0）。

## 用户那边会发生什么

- **本地版**：看板 12 小时内会查到新版本。报头下面提示“有新版本”，点“更新”就会下载新代码、装依赖、自动重启，页面自己刷新。
  开着“自动更新”时（默认开），会在看板 10 分钟没人用、也没在生成推荐的时候自己更新。
  用 git 装的切到新标签；下载压缩包装的（Windows）替换代码文件夹；用户改过代码就不动，只提示。
- **云端版**：看板提示有新版本，用户到自己 fork 的仓库页面点 **Sync fork**，Cloudflare 会自动重新部署。

## 改数据库结构时

- 本地版：在 `core/src/robot_times/cloud/store.py` 的 `MIGRATIONS` 末尾追加 SQL（只追加、不改旧的），同时改 `schema.sql`。
- 云端版：在 `web/app/migrations/` 加一个新文件，并在更新说明里提醒用户运行 `npx wrangler d1 migrations apply DB --remote`。
