# 豪哥论文：阿里云 Linux 部署

适用于 Alibaba Cloud Linux 3、已有或可安装的 Nginx、systemd。使用发行版 Python 3.11 的独立虚拟环境，不替换系统 Python。源代码放在 `/opt/haoge-papers`，然后以 root 执行：

```bash
cd /opt/haoge-papers
bash deploy/install-alinux.sh 你的公网IP
```

安装脚本会先运行测试，再启动服务。仅为给定 IP 新增 Nginx 虚拟主机，已有其他域名配置保留。后端始终监听 `127.0.0.1:8766`；对外通过 Nginx 的 80 端口访问，需要在安全组中允许相应的入站端口。

- 无需账号或共享密码，打开即用。每个浏览器通过随机的私有 Cookie 访问独立空间，其他访客不能访问该空间的数据和 PDF。
- 独立资料位于 `/var/lib/haoge-papers/spaces/<空间ID>/`；页面可下载专属空间入口，用于换设备或清除 Cookie 后恢复访问。
- 旧共享资料会复制到所有者空间；专属恢复信息位于 `/var/lib/haoge-papers/.owner-space.json`。该文件与 `.space-secret` 不可上传 Git、公开展示或通过 Nginx 静态目录暴露。
- 初始配置为 HTTP。长期或经不可信网络使用前，应给 Nginx 配置 HTTPS，避免网络窃听匿名空间的访问凭据。后端 HTTPS 代理头会让 Cookie 自动添加 Secure。
- 服务器端收藏、历史、题录及 PDF 保存在 `/var/lib/haoge-papers`；不自动上传本机的历史和收藏。
- 服务账号为 `haogepapers`，以普通权限运行，开机启动、异常重启，最大内存为 512 MiB。

维护：

```bash
systemctl status haoge-papers
journalctl -u haoge-papers -n 80 --no-pager
systemctl restart haoge-papers
systemctl stop haoge-papers
```

升级：先备份服务器数据，更新 `/opt/haoge-papers` 的代码及依赖，运行测试，再重启服务。备份 SQLite 时先停止 `haoge-papers` 服务，复制完整数据目录后再启动。

GitHub 私有仓库的凭据不需要留在服务器；可通过 SSH/SCP 上传 `git archive` 导出的纯源代码压缩包。
