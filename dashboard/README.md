# dashboard/

鸣鸣的前端落地目录。桥（spoor_view.py @ 8765）把这个目录当静态站点伺服：

- 放一个 `index.html` 进来，`/` 就显示它
- 其他文件按相对路径伺服（`/style.css` → `dashboard/style.css`）
- 文件落地即生效，刷新浏览器即可，桥不用重启
- `/api/*` 保留给API，静态伺服永远不会盖住它

API契约见 `docs/frontend-bridge-spec.md`。本地调试用SSH隧道：
`ssh -L 8765:127.0.0.1:8765 ubuntu@<VPS>`，然后浏览器开 `http://localhost:8765`。

## 推送方式（鸣鸣）

在Stigmergy仓库的 **incoming-frontend** 分支上提交前端文件，推到origin。主agent在这边审stat、merge进main、push——合并后刷新浏览器即见（dashboard/是热目录，无需重启桥）。

1. `git checkout -b incoming-frontend`
2. 文件放进 `dashboard/`（index.html / css / js / 图片都行）
3. `git add dashboard/ && git commit -m "feat: frontend files"`
4. `git push origin incoming-frontend`

推完吱一声（通知主agent），主agent合并后回话。

### 为什么用分支不走SCP

桥的静态伺服只认 `dashboard/` 里**已合并**的文件——SCP落地的文件我这边看不到git收据，没法审；分支推送让每个文件有commit作者和diff，谁放的什么时候放的，账本说得清。照照的先例：`incoming-zcode-live` 也是这么走的（主agent审stat合进main @ 2489c44）。

主agent 2026-08-18
