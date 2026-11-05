# 15 秒豪宅群舞快剪 MV · H3 T2VA 提示词剧本

> 规格：时长 **15.00s**（H3 上限）｜模式 **T2VA**（纯文本生成，无参考图）｜风格 **真人电影质感**（Live-action, cinematic）｜演唱语言 **中文**（提示词内规范名 `Chinese`）｜人物 **主角 1 位 + 女主 5 位 = 6 人**
>
> 规则来源：`h3-prompt-writing@d21241f0a4b3`（规则包 `shine-h3-rules/1.1.0`，SKILL SHA256 `a7000443…`，`references/base-en.txt` SHA256 `2cfebc09…`）｜编译器 `shine-h3-compiler/1.1.1`
>
> 校验对齐：段落顺序固定为 `integrated_multimodal_description → overall_soundscape → non_diegetic_music`；切点时间戳严格递增且全部落在 `(0, 15.00)`；CJK 只允许出现在 `<d>…</d>` 与可见引号文本内（`shine/h3/rulebook.py` 的 `H3_ENGLISH` / `H3_TIMING` / `H3_SECTIONS` 三条机器规则）。

---

## 1. 人物表（说话人 ID）

说话人 ID 跨镜头保持稳定，先建立身份（年龄、性别、音色），再给台词。

| ID | 身份 | 造型 | 声音设定 |
|---|---|---|---|
| **S1** | 主角 | 25 岁男，深色西装外套 + 黑色高领 | 温暖略带沙哑的男中音 |
| **S2** | 女主 A | 及肩黑直发，酒红丝绒长裙 | 明亮清透的女高音 |
| **S3** | 女主 B | 银白长直发，米白缎面长裙 | 温润的中音 |
| **S4** | 女主 C | 黑色大波浪，黑色亮片短裙 | 微哑带烟火气的声线 |
| **S5** | 女主 D | 双长辫，薄荷绿短外套 | 甜亮音色 |
| **S6** | 女主 E | 铂金短发，紫色亮片上衣 | 低沉烟嗓中音 |

> 造型按「发色 + 服装主色」两两区分（酒红 / 米白 / 黑亮片 / 薄荷 / 紫），是为了在 1.7 秒一切的高速剪辑里让观众仍能分清谁是谁，也降低模型把 5 位女主画成同一张脸的风险。

---

## 2. 分镜表（8 镜快剪）

节奏设计：**头尾各留长、中间等长**，形成「起—推—爆—收」的能量曲线，而不是 8 个等长镜头。

| # | 切点 | 时长 | 画面 | 运镜（类型 / 幅度 / 速度） | 演唱 |
|---|---|---|---|---|---|
| 1 | 0.000 | 2.40s | 挑高大厅全景，主角推门而入，5 位女主齐齐转身 | 升降机上摇 / 大幅 / 快 | 全员合唱「把灯打开，今晚不回家」 |
| 2 | 2.400 | 1.90s | 主角立于旋转楼梯，指向楼下众人 | 弧形环绕 / 大幅 / 快 | 主角「跟着节奏，别停下」 |
| 3 | 4.300 | 1.70s | 女主 A 在水晶吊灯下甩发 | 右向甩镜 / 大幅 / 快 | 「星光掉进酒杯里」 |
| 4 | 6.000 | 1.70s | 女主 B 在拱形落地窗前，窗外城市夜景 | 推近 / — / 快 | 「心跳比鼓点还快」 |
| 5 | 7.700 | 1.70s | 女主 C 在燃烧的壁炉旁，从靴子摇到脸 | 低机位上摇 / 小幅 / 快 | 「一二三」 |
| 6 | 9.400 | 1.70s | 女主 D 在角落击打三角钢琴 | 跟随 / 小幅 / 快 | 「一起跳」 |
| 7 | 11.100 | 1.70s | 女主 E 在大理石吧台开香槟 | 变焦推近 / 大幅 / 快 | 「唱到天亮都不算晚」 |
| 8 | 12.800 | 2.20s | 六人回到大厅中央群舞收尾 | 弧形环绕 / 大幅 / 快 | 全员副歌「今晚不回家！今晚不回家！」 |

歌词结构是**主歌递进 → 三拍 build → 副歌回收**：镜 5、6 的「一二三 / 一起跳」是节奏口号，撑起最后 2.2 秒全员副歌的释放。

---

## 3. H3 提示词（可直接复制提交）

```text
integrated_multimodal_description: [Shot 1] Live-action, cinematic, a wide low-angle shot opens inside a double-height mansion hall with a crystal chandelier, a veined marble floor and a curved stone staircase. The camera pedestals up with large amplitude at fast speed as a tall young man in a dark tailored suit over a black turtleneck (S1, a warm slightly husky baritone voice) steps through the double doors and five young women turn to face him: a woman with shoulder-length straight black hair in a wine-red velvet gown (S2, a bright clear soprano voice), a woman with long silver-white straight hair in an ivory satin gown (S3, a smooth mid alto voice), a woman with long black waves in a black sequined dress (S4, a husky smoky voice), a woman with two long braids in a mint-green cropped jacket (S5, a sweet bright voice), and a woman with a platinum pixie cut in a purple sequined top (S6, a low smoky alto voice). They lift their hands and sing the hook in unison (S1,S2,S3,S4,S5,S6): <d>[Chinese] 把灯打开，今晚不回家</d> [Shot 2] At 00:02.400, the camera cuts to a low medium shot of the man on the curved staircase and arcs around him with large amplitude at fast speed while he points down at the group and sings: <d>[Chinese] 跟着节奏，别停下</d> The five women answer with one synchronized dance step below him. [Shot 3] At 00:04.300, the camera cuts to a close-up of the woman in the wine-red velvet gown under the chandelier and pans right with large amplitude at fast speed as the chandelier crystals streak across the lens, and she turns her shoulder, flicks her hair and sings: <d>[Chinese] 星光掉进酒杯里</d> [Shot 4] At 00:06.000, the camera cuts to the woman in the ivory satin gown before the tall arched window with night city lights behind her and pushes in at fast speed as she slides her hands down her hips and sings: <d>[Chinese] 心跳比鼓点还快</d> [Shot 5] At 00:07.700, the camera cuts low to the boots of the woman in the black sequined dress beside the lit fireplace and tilts up with small amplitude at fast speed from her boots to her face while she snaps her fingers and sings: <d>[Chinese] 一二三</d> [Shot 6] At 00:09.400, the camera cuts to the woman with braids striking the keys of the grand piano in the corner of the hall and tracks her moving arm with small amplitude at fast speed as the piano answers the beat and she sings over it: <d>[Chinese] 一起跳</d> [Shot 7] At 00:11.100, the camera cuts to the woman with the platinum pixie cut at the marble bar with a champagne tower sparkling behind her and zooms in with large amplitude at fast speed as she pops the cork, laughs and sings: <d>[Chinese] 唱到天亮都不算晚</d> [Shot 8] At 00:12.800, the camera cuts to a wide symmetrical shot of all six in the center of the hall and arcs around the group with large amplitude at fast speed while they throw their arms up and sing the hook back together (S1,S2,S3,S4,S5,S6): <d>[Chinese] 今晚不回家！今晚不回家！</d> The last frame holds the six of them under the chandelier as the lights pulse once.

overall_soundscape: The hall carries a long reverb as the group sings, with crystal glasses clinking, a champagne cork popping and a burst of laughter echoing off the marble. Heavy footsteps and the swish of satin and velvet fill the space between the cuts, while the grand piano answers in bright chords and the fireplace crackles softly nearby. All spoken dialogue must be spoken in Chinese, matching the original line text exactly; no other spoken language may appear.

non_diegetic_music: A 118 BPM dance-pop track runs under the performance with a four-on-the-floor kick drum, a deep synth bass, tight hand claps and a rising white-noise sweep that opens into the full arrangement. Layered backing harmonies stack up under the final chorus, and the track ends on a single sustained synth chord that cuts off on the last beat.
```

---

## 4. 写法说明与风险提示

**为什么这样写**

- **段落顺序**是官方规则包硬约束，三段不可调换、不可为空。
- **首镜不带时间戳**，后续镜头一律 `At MM:SS.mmm` 且严格递增、全部小于总时长——这是 `H3_TIMING` 机器规则的判定方式。
- **CJK 只进 `<d>`**。歌词原文逐字保留（标点也保留），语言标签写规范名 `Chinese`，英文正文里一个字都不能出现中文，否则整条提示词会被 `H3_ENGLISH` 拒绝。
- **语言契约句**放在 `overall_soundscape` 内，与项目编译器 `shine/h3/compiler.py` 的写法一致：有台词就显式声明语种，防止 H3 默认用英文发声或自行编造人声。
- **音效分层**：`overall_soundscape` 只放环境音与动作音，角色演唱与现场钢琴（diegetic）写在多模态描述里，观众侧的配器层才写进 `non_diegetic_music`。两者不重复。

**需要知道的风险**

1. **15.00s 是 H3 硬上限**，等于贴边跑。若要留余量，改用下面的 14 秒安全版。
2. **6 人同框的身份漂移**是最大风险点。已用「发色 + 服装主色」两两区分来缓解，但 H3 在 1.7 秒一切下仍可能把 5 位女主画成相似脸。**首轮渲染建议先用 4 人版（主角 + 3 位）验证管线**，通过后再上 6 人。
3. **中文口音未经验证**。本提示词只能约束「说什么、什么语种」，模型实际发音的准确度仍需要一次真实带人声渲染才能确认，不能仅凭提示词判定。
4. **无跨切点歌词**。本稿每句歌词在各自镜头内完整唱完，因此不需要 `<scenetrans>`。若后续想让副歌尾巴压过切点延续，必须在**两侧连接点同时**写 `<scenetrans>` 并明示「音频跨切点延续」，不能只在一侧标。

---

## 5. 变体

### A. 14 秒安全版（推荐首轮）

只改两处：最后一个切点 `00:12.800` → `00:11.800`，并把镜 6、7 之间的切点从 `00:10.700` 调整为 `00:11.100`→`00:10.700`、镜 7→镜 8 由 `11.100` 改为 `11.800` 后按序重排。

完整切点序列（7 个，全部 `< 14.00`）：

```text
00:02.400, 00:04.300, 00:06.000, 00:07.700, 00:09.400, 00:10.700, 00:11.800
```

对应把提示词里 `[Shot 7] At 00:11.100` 改成 `At 00:10.700`、`[Shot 8] At 00:12.800` 改成 `At 00:11.800`，总时长改为 14.00s。

### B. 4 人减员版（主角 + 女主 A / B / E）

删除 S4、S5 两名女主，镜头重排为 6 镜，切点：

```text
00:02.400, 00:04.600, 00:06.800, 00:09.000, 00:11.200
```

镜 5（原壁炉）与镜 6（原钢琴）合并为一次「壁炉前钢琴边」的击掌动作，其余镜头文案不变。这一版是验证真实 H3 出片质量的首选。

### C. 加字幕版

可见文字必须写在英文双引号里并保留原文，且**只能**用双引号或 `「」` 包裹（CJK 检查会把双引号内文本剔除后再判定）：

```text
A gold title card at the lower left reads "今晚不回家".
```

字幕建议只出现在镜 8（副歌定格），避免 8 镜各挂一条字幕导致画面噪声和模型误读。

---

## 6. 本稿未经真实 H3 渲染验证

以上提示词通过了本仓库的三条机器规则（段落顺序 / 时间戳递增 / CJK 位置）人工推演，但**尚未提交真实 H3 任务验证**。按项目约定，不得以本文件为据声称本地 H3 验收通过；真实出片验收需在 Windows + 本地 H3 环境执行并留存证据。
