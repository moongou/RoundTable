# RoundTable 运维面板使用方法（本地 + ECS）

## 1. 能力说明

现在有两套入口，但能力已对齐：

- 本地开发面板：`http://localhost:8888`
- 后端应用入口：`http://localhost:8001`
- 管理后台入口：`/admin/`
- 用户统计能力：已并入 8888 面板（总用户、活跃用户、累计场次、用户详情、删除、CSV 导出）

在 ECS 上，新增了受保护入口：`/ops/`，会代理到服务器本机 `127.0.0.1:8888`。

## 2. 本地使用

1. 启动开发面板

```bash
cd /path/to/RoundTable
node devpanel.js
```

1. 打开页面

- 面板：[http://localhost:8888](http://localhost:8888)
- 应用：[http://localhost:8001](http://localhost:8001)
- 管理后台：[http://localhost:8001/admin/](http://localhost:8001/admin/)

1. 在 8888 面板中使用“用户使用统计”

- 点击“登录统计”
- 使用 `/admin` 同一套账号（默认可用 `admin`）
- 登录后可查看统计、搜索用户、看详情、删用户、导出 CSV

## 3. ECS 首次部署（推荐）

首次部署时指定运维面板令牌：

```bash
ssh root@<ECS_IP> "OPS_PANEL_TOKEN='<你的强口令>' bash -s" < deploy/deploy_aliyun.sh
```

脚本会完成：

- 安装 Node.js
- 启动后端服务 `roundtable`
- 启动面板服务 `roundtable-devpanel`（监听 `127.0.0.1:8888`）
- Nginx 增加受保护入口 `/ops/`

## 4. ECS 访问方式

### 4.1 浏览器直接访问（query token）

- 运维面板：`https://<你的域名>/ops/?token=<OPS_PANEL_TOKEN>`
- 管理后台：`https://<你的域名>/admin/`

### 4.2 API 验证（header token）

```bash
curl -H 'X-Ops-Token: <OPS_PANEL_TOKEN>' https://<你的域名>/ops/api/status
curl -H 'X-Ops-Token: <OPS_PANEL_TOKEN>' https://<你的域名>/ops/api/admin/stats
```

### 4.3 SSH 隧道方式（不暴露公网 /ops）

```bash
ssh -L 8888:127.0.0.1:8888 root@<ECS_IP>
```

然后本机打开 [http://localhost:8888](http://localhost:8888)。

## 5. 一键轮换 /ops Token

### 5.1 在 ECS 机器上直接执行

```bash
OPS_PANEL_TOKEN='<NEW_TOKEN>' /opt/roundtable/deploy/rotate_ops_token.sh
```

### 5.2 在本地一条命令远程执行

```bash
ssh root@<ECS_IP> "OPS_PANEL_TOKEN='<NEW_TOKEN>' /opt/roundtable/deploy/rotate_ops_token.sh"
```

脚本行为：

- 自动备份当前 Nginx 配置
- 自动替换 `/ops` 的 header/query token
- 自动执行 `nginx -t` 校验
- 校验通过后自动 `reload nginx`
- 若校验失败会自动回滚

## 6. 日常更新发布

```bash
./deploy/deploy.sh
```

该脚本会：

- 同步后端代码
- 同步 `devpanel.js`
- 重启 `roundtable` 和 `roundtable-devpanel`（若服务已创建）

## 7. 常见问题

### 7.1 访问 `/ops/` 返回 403

原因：token 不正确或未携带。

处理：检查 `X-Ops-Token` 或 `?token=`。

### 7.2 `/ops/` 打不开

检查：

```bash
systemctl status roundtable-devpanel
systemctl status nginx
journalctl -u roundtable-devpanel -f
```

### 7.3 统计区提示未登录 / 401

先在面板内点击“登录统计”，并确认后端 `8001` 已启动。

### 7.4 旧链接 `admin.html` / `admin.htm` 无法使用

已兼容跳转到 `/admin/`，如仍异常请重启后端服务。
