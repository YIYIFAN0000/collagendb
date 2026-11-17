# CollagenDB · Colformer Atlas

胶原编码序列与羟脯氨酸（Hyp）来源证据静态图谱。

目标域名：<https://collagendb.com>。项目源于 `Colformer_Atlas_Portable_v3.zip`，网站与数据文件保持原样。

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

## GitHub Pages

发布来源为 `main` 分支的根目录 `/`，`.nojekyll` 用于按原样发布静态资源。更新 `main` 后由 GitHub Pages 自动重新部署。

自定义域名使用 `collagendb.com`；DNS 在阿里云管理。域名设置与 HTTPS 状态以仓库 **Settings → Pages** 为准。

## 完整性与使用边界

`FILES_SHA256.json` 为原便携包的文件校验清单，`downloads/checksums.json` 为下载文件校验清单。部署元数据（README、CNAME、.nojekyll 等）不在原始清单内。

未注释位置的修饰状态未知，不能作为未修饰负样本；来源记录不能直接视为独立实验次数。本仓库未对第三方来源数据额外授予许可，请遵守各来源的使用条款并引用原始来源。
