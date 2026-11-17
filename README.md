# CollagenDB · Colformer Atlas

胶原编码序列与羟脯氨酸（Hyp）来源证据静态图谱。

正式网站：<https://collagendb.com>，也可使用 <https://www.collagendb.com>。项目源于 `Colformer_Atlas_Portable_v3.zip`，网站与数据文件保持原样。

## 内容

- 75,701 条不同编码序列，按需加载序列和位点注释分块。
- 4,283 条先导训练序列，以及独立 Hyp 证据与参考序列下载。
- 数据来源、去重方法与使用边界见 [方法与来源](about.html)。

## 本地运行

无需安装前端依赖或执行构建。在项目目录运行：

```sh
python open_atlas.py
```

也可用静态 HTTP 服务器，或直接打开 `index.html`。请完整保留 `data/` 和 `downloads/`。

## 阿里云服务器部署

正式站点运行在已有的阿里云香港 ECS `8.218.195.119`，GitHub 保存网站源码。两个域名均已启用 HTTPS，HTTP 自动跳转至 HTTPS。

证书由 Let's Encrypt 签发，`collagendb-certbot-renew.timer` 每天检查两次续期；续期模拟测试已通过。GitHub 源码更新与服务器发布是独立操作，推送到 GitHub 不会自动更新阿里云站点。

阿里云 DNS 中，`@` 和 `www` 均使用 A 记录指向 `8.218.195.119`。服务器需要允许网站的 TCP 80、443 端口。

部署工具与注意事项见 [服务器部署说明](deploy/README.md)。首次部署会先校验原始文件，优先复用宝塔 Nginx；同名已有站点或不明服务冲突会中止。

GitHub Pages 可作为独立预览，地址为 <https://yiyifan0000.github.io/collagendb/>。正式域名没有绑定到 GitHub Pages；更新 GitHub 不代表阿里云服务器已更新。

## 完整性与使用边界

`FILES_SHA256.json` 为原便携包的文件校验清单，`downloads/checksums.json` 为下载文件校验清单。部署元数据（README、CNAME、.nojekyll 等）不在原始清单内。

未注释位置的修饰状态未知，不能作为未修饰负样本；来源记录不能直接视为独立实验次数。本仓库未对第三方来源数据额外授予许可，请遵守各来源的使用条款并引用原始来源。
