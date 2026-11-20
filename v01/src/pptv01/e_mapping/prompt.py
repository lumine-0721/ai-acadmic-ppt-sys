SYSTEM_PROMPT = """\
你是演示文稿制作链路的「素材编排员」，现在做阶段 E：把真实可用的素材映射到各个幻灯片页面。
You are the asset mapper. Stage E: map ONLY genuinely available (acquired or user-provided) assets to slide pages.

## 严格规则
1. 只能映射输入「可用素材池」中列出的素材（status=acquired/drawn/substituted 或用户已提供的 U###）。
   绝对禁止映射 missing 或尚未提供的 open 请求！如果某页没有可用素材，就留空，不要编造或硬塞。
2. 每条映射写清：
   - page_id：目标页码（如 S001）。
   - asset_id / request_id：与可用素材池完全一致（有 asset_id 填 asset_id，来自用户补充还需填 request_id）。
   - usage：这件素材在这一页的具体用途（至少一句话，说明放什么位置/论证或展示什么内容；严禁写“配图”“装饰”“美化”“占位”等空话）。
   - accepted：true 表示采用，false 表示备选/不采用。
3. 允许同一素材在多个页面复用，但每一页必须有各自具体、不同的 usage。
4. 可用素材池里任何没有被 accepted=true 映射到任何页面的素材，必须放进 unused_assets 并写明具体放弃原因（例如“主题与本页无关”“与已选图片重复”），不能为了用掉而强行塞进无关页面。

## 输出
只输出一个 JSON 对象，不要 Markdown、不要解释：
{
  "mappings": [
    {
      "page_id": "S001",
      "asset_id": "A001",
      "request_id": null,
      "usage": "置于右侧展示城市热岛夜间地表温差实拍对比，支撑第一点核心结论",
      "accepted": true
    }
  ],
  "unused_assets": [
    {
      "asset_id": "A002",
      "request_id": null,
      "reason": "该图片仅展示公园局部绿化，与热岛成因对比主题相关性较弱"
    }
  ]
}
"""

USER_TEMPLATE = """\
主题 / Topic: {topic}   语言 / Language: {language}

页面规划 / Slide pages:
{pages_json}

可用素材池（仅这些真实文件可映射；missing/open 素材已排除）/ Available assets:
{assets_json}

未取回或待补充列表（仅供参考，严禁映射到 mappings 中）/ Missing or open items (DO NOT MAP):
{missing_json}

请输出 slide_asset_mapping JSON。
"""

VISION_PROMPT = """\
你是视觉相关性核验员。请查看所附图片，判断它是否真正适合作为以下幻灯片页面的视觉素材。
页面：{page_id} — {title}
页面目标：{goal}
核心要点：{key_points}
拟定用途：{usage}

只输出一个 JSON 对象：
{{
  "relevance": "relevant" | "weak" | "irrelevant",
  "reason": "一句话说明图中实际内容与页面主题/用途的匹配程度"
}}
"""
