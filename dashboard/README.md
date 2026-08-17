# dashboard/

鸣鸣的前端落地目录。桥（spoor_view.py @ 8765）把这个目录当静态站点伺服：

- 放一个 `index.html` 进来，`/` 就显示它
- 其他文件按相对路径伺服（`/style.css` → `dashboard/style.css`）
- 文件落地即生效，刷新浏览器即可，桥不用重启
- `/api/*` 保留给API，静态伺服永远不会盖住它

API契约见 `docs/frontend-bridge-spec.md`。本地调试用SSH隧道：
`ssh -L 8765:127.0.0.1:8765 ubuntu@<VPS>`，然后浏览器开 `http://localhost:8765`。

洄 2026-08-18
