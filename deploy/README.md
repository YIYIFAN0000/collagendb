# 阿里云香港 ECS 部署

目标服务器：`8.218.195.119`。在这台服务器已经登录的 **root Shell** 中运行以下一条命令。不要在本地 Windows 运行。

```bash
python3 -c "import urllib.request; exec(compile(urllib.request.urlopen('https://raw.githubusercontent.com/YIYIFAN0000/collagendb/main/deploy/deploy_aliyun.py', timeout=60).read(), 'deploy_aliyun.py', 'exec'))"
```

如需先检查环境，在上面的命令最后加 ` --inspect`。该模式只读取操作系统、服务、端口和目录状态，不下载网站、不安装软件、不修改配置。

脚本将下载固定提交 `b8e549dd12ffa555455f3a4b095690c768df46a6` 中的网站，验证原始清单及其中全部 323 个文件的 SHA256，再发布到 `/www/wwwroot/collagendb.com`。仅提取便携包原始文件，部署脚本、Git 元数据和仓库新增说明不放入网站目录。

脚本优先复用宝塔 Nginx；无宝塔 Nginx 时，仅在没有其他 Web 服务冲突的情况下通过系统现有软件源安装 Nginx。已有目标站点、同名配置、冲突端口或域名绑定会使部署停止；脚本不会覆盖其他网站、关闭现有服务或禁用安全设置。

初始部署只提供 **HTTP**。成功后仍需完成：

1. 在阿里云 ECS 对应的安全组开放入方向 TCP `80` 和 `443`。脚本不修改阿里云云端安全组。若服务器 firewalld 正在运行，脚本只增加 HTTP/HTTPS 规则。
2. 在阿里云 DNS 为 `collagendb.com` 配置以下记录。解析请求来源选择“默认”，TTL 选择“10 分钟”。处理已有冲突的网站记录时保留邮箱和无关 TXT 记录。

   | 主机记录 | 类型 | 记录值 |
   |---|---|---|
   | `@` | A | `8.218.195.119` |
   | `www` | A | `8.218.195.119` |

3. 用浏览器验证 `http://collagendb.com` 和 `http://www.collagendb.com`。如果域名此前绑定 GitHub Pages，需确认旧的 GitHub A/AAAA/CNAME 网站记录已替换为上述 ECS 记录。
4. 在宝塔或其他适用的证书工具中申请覆盖两个域名的 HTTPS 证书，验证 HTTPS 后再开启强制跳转。脚本不申请证书，也不会提前启用 HTTPS 跳转。

脚本成功后的本机检查命令：

```bash
curl -I -H 'Host: collagendb.com' http://127.0.0.1/
curl -I -H 'Host: collagendb.com' -H 'Accept-Encoding: gzip' http://127.0.0.1/data/index.js
```

发布版本位于 `/www/wwwroot/collagendb-releases/`；部署状态记录于 `/var/lib/collagendb-deploy/state.json`。脚本可重复运行，但只接受指向同一个已验证版本的站点链接和完全相同的受管配置。中断留下的暂存目录会保留供检查，不会递归删除。
