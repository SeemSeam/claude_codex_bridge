<div align="center" dir="rtl">

# CCB - وصل تطبيق الهاتف!

**واجهة TUI خفيفة لعدة وكلاء، مع طبقة تعاون مستقرة عبر المزوّدين**<br>
**نسّق Codex وClaude وGemini وغيرهم من وكلاء CLI ضمن سير عمل مرئي وقابل للتحكم والتدخل المباشر**

<p>
  <img src="https://img.shields.io/badge/version-8.6.7-orange.svg" alt="version">
  <img src="https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20WSL-lightgrey.svg" alt="platform">
  <img src="https://img.shields.io/badge/providers-17%20CLI%20families-0B7285.svg" alt="providers">
</p>

<p>
  <img src="https://img.shields.io/badge/Codex-111111?style=flat-square&logo=openai&logoColor=white" alt="Codex">
  <img src="https://img.shields.io/badge/Claude-D97757?style=flat-square&logo=anthropic&logoColor=white" alt="Claude">
  <img src="https://img.shields.io/badge/Gemini-4285F4?style=flat-square&logo=googlegemini&logoColor=white" alt="Gemini">
  <img src="https://img.shields.io/badge/Grok-000000?style=flat-square&logo=x&logoColor=white" alt="Grok CLI">
  <img src="https://img.shields.io/badge/Kimi-111111?style=flat-square&logo=moonshotai&logoColor=white" alt="Kimi">
  <img src="https://img.shields.io/badge/MiMo-FF6900?style=flat-square&logo=xiaomi&logoColor=white" alt="MiMo">
  <img src="https://img.shields.io/badge/Qwen-6A5CFF?style=flat-square" alt="Qwen">
  <img src="https://img.shields.io/badge/Cursor-111111?style=flat-square" alt="Cursor">
  <img src="https://img.shields.io/badge/Copilot-111111?style=flat-square&logo=githubcopilot&logoColor=white" alt="GitHub Copilot">
  <img src="https://img.shields.io/badge/Crush-FF5A5F?style=flat-square" alt="Crush">
  <img src="https://img.shields.io/badge/Kiro-6D5EF6?style=flat-square" alt="Kiro">
  <img src="https://img.shields.io/badge/Pi-111111?style=flat-square" alt="Pi">
  <img src="https://img.shields.io/badge/Z.ai-111111?style=flat-square" alt="Z.ai">
  <img src="https://img.shields.io/badge/OpenCode-111111?style=flat-square" alt="OpenCode">
  <img src="https://img.shields.io/badge/Antigravity-6D5EF6?style=flat-square&logo=google&logoColor=white" alt="Antigravity">
  <img src="https://img.shields.io/badge/Droid-3DDC84?style=flat-square&logo=android&logoColor=white" alt="Droid">
</p>

[中文](zh.md) | [English](../README.md) | [日本語](ja.md) | [Français](fr.md) | [Deutsch](de.md) | **العربية** | [Español](es.md) | [Português](pt.md) | [한국어](ko.md) | [Русский](ru.md)

[البدء السريع](#quick-start) · [Mobile App](#mobile-app) · [وضع Rich](#rich-mode) · [إعداد الوكلاء](#configure-agents) · [دليل المستخدم](../docs/manuals/user-guide/) · [دليل المطور](../docs/manuals/developer-guide/)

<p align="center">
  <img src="../assets/readme_v7/ccb-hero-en-light.png" alt="مساحة عمل CLI مرئية متعددة الوكلاء في CCB" width="960">
</p>

</div>

<a id="why-ccb"></a>

## لماذا CCB؟

- اتصال مستقر بين الوكلاء لرسوم تعاون معقدة مثل `A -> B -> C` و `A,B -> C` و `A -> B,C`.
- كل agent هو طرفية أصلية كاملة مع تحكم مرئي في التخطيط وإمكانية تدخل مباشر.
- يحافظ daemon الخلفي على حالة المشروع حتى عند إغلاق واجهة المقدمة.
- قدرة Hub: تشغيل عدة CLI providers بالتوازي من أمر واحد.
- متحكم هاتف بعيد: تحكم صوتي عبر providers، ونقل ملفات، ووصول إلى طرفية بعيدة.

<a id="how-to-install"></a>

## طريقة التثبيت

ثبّت أو حدّث نسخة CCB التي يديرها npm باستخدام npm:

```bash
npm install -g @seemseam/ccb@latest
```

للتثبيت من GitHub release أو المصدر استخدم updater المعاملاتي في CCB:

```bash
ccb update
```

في التثبيت الذي يديره npm يعرض `ccb update` أمر npm المكافئ ولا يعدّل payload المضمّن.

<details>
<summary><b>حزم GitHub release وخيار التثبيت من المصدر</b></summary>

إذا كان npm غير مناسب في بيئتك، نزّل الحزمة المناسبة من [Releases](https://github.com/SeemSeam/claude_codex_bridge/releases)، ثم فك الضغط وثبّت:

```bash
tar -xzf ccb-*.tar.gz
cd ccb-*
./install.sh install
```

التثبيت من المصدر مخصص للتطوير أو كحل مؤقت فقط:

```bash
git clone https://github.com/SeemSeam/claude_codex_bridge.git
cd claude_codex_bridge
./install.sh install
```

يربط التثبيت من المصدر أوامر `ccb` / `ask` العالمية بالـ checkout الحالي. يفضّل المستخدمون العاديون حزمة npm.

</details>

<a id="quick-start"></a>

## البدء السريع

### 1. التشغيل

نفّذ الأمر من مجلد العمل:

```bash
ccb
```

إذا أخبرك التشغيل أنه لا يمكن إنشاء `.ccb` تلقائيا أو أن مرساة المشروع مفقودة، أنشئ `.ccb` يدويا:

```bash
mkdir -p .ccb
```

<a id="configure-agents"></a>

### 2. إنشاء إعداد المشروع

يبدأ المشروع الفارغ بشكل خفيف: يفتح CCB نافذة `main` واحدة فقط، ويختار أول CLI مدعوم متاح على الجهاز، وينشئ agent واحدا باسم `demo`. لم يعد فريق متعدد الوكلاء يركب افتراضيا.

انقر على **⚙ الإعدادات** في أعلى يسار sidebar الخاصة بـ CCB لفتح لوحة الإعداد المحلية. ويمكن أيضا تشغيلها عبر `ccb config ui`.

<p align="center">
  <img src="../assets/readme_v7/config-control-panel.png" alt="لوحة إعداد CCB للـ agent الافتراضي demo" width="960">
</p>

تتيح اللوحة إعداد windows وتقسيم panes وproviders والنماذج ومستويات thinking وAPI overrides وworkspaces ووضع Rich وsidebar. وتتحقق من التغييرات قبل الحفظ، مع reload dry-run وhot reload محمي.

لطوبولوجيا متعددة الوكلاء متقدمة، أضف agents بصريا أو أنشئ `.ccb/ccb.config` يدويا. يتحكم `,` و `;` في التكديس العمودي والتقسيم الأفقي، ويقارب `A,B;C,D` تخطيط أربع panes.

```toml
version = 2

[windows]
main = "main:codex"
work = "worker1:codex(worktree), worker2:claude(worktree)"
review = "reviewer:claude, qa:gemini"

[ui.sidebar]
mode = "every_window"
width = "15%"
bottom_height = 20
agents_height = "50%"
comms_height = "15%"
tips_height = "35%"
comms_limit = 3
```

تحقق من الإعداد ثم ابدأ مساحة العمل:

```bash
ccb config validate
ccb
```

### 3. التعاون

يمكنك الكتابة مباشرة في أي agent pane، أو جعل الوكلاء يتعاونون:

```text
/ask reviewer review the latest parser changes and list blocking issues.
```

يمكن للوكلاء أيضا استدعاء `/ask` أثناء تنسيق workflow للتفويض والتسليم. استخدم ذاكرة agent أو ملف الذاكرة المشتركة للمشروع `.ccb/ccb_memory.md` للتنسيق المستمر.

<a id="mobile-app"></a>

## التحكم البعيد من الهاتف (Android)

الطريقة الموصى بها للتحكم في CCB من الهاتف يمكنها الاتصال بكل مشاريع CCB، والتحكم بكل agent، وقبول الإدخال الصوتي، ونقل الملفات.

```bash
ccb update mobile
```

يرشدك هذا الأمر خلال التثبيت والإعداد.

<p align="center">
  <img src="../assets/readme_v7/mobile-control-chat.jpg" alt="محادثة agent في CCB Mobile" width="180">
  <img src="../assets/readme_v7/mobile-control-terminal.jpg" alt="تحكم terminal في CCB Mobile" width="180">
  <img src="../assets/readme_v7/mobile-control-files.jpg" alt="نقل ملفات CCB Mobile" width="180">
  <img src="../assets/readme_v7/mobile-control-pairing.jpg" alt="اقتران واتصال CCB Mobile" width="180">
</p>

<details>
<summary><b>تفاصيل Mobile App وحدود الأمان والمصدر</b></summary>

يتضمن CCB 8.6.7 مصدر Flutter الخاص بـ CCB Mobile داخل [`mobile/`](../mobile/) وينشر Android APK عبر GitHub Releases:

- [تنزيل CCB Mobile v8.6.7 APK](https://github.com/SeemSeam/claude_codex_bridge/releases/download/v8.6.7/ccb-mobile-v8.6.7.apk)
- مصدر التطبيق: [`mobile/app`](../mobile/app)
- مصدر gateway الخادم: [`lib/mobile_gateway`](../lib/mobile_gateway)

تطبيق الهاتف هو متحكم بعيد لمشاريع CCB حقيقية تعمل على خادم. يمكنه اكتشاف المشاريع المركبة من server-wide mobile gateway، والتبديل بين windows و agents، وعرض سياق محادثة agent، وإرسال النص عبر pane-native input، وفتح terminal view، ورفع/تنزيل الصور والمستندات عبر gateway موثق.

حدود الأمان:

- يربط CCB gateway على loopback فقط، مثل `127.0.0.1:8787`.
- يستخدم الوصول البعيد Tailscale Serve وليس Tailscale Funnel.
- لا يخزن CCB كلمات مرور Tailscale أو OAuth tokens أو admin API tokens، ولا يغير tailnet ACLs/grants تلقائيا.
- يحصل الهاتف فقط على scopes المصرح بها في pairing profile، مثل view و content و terminal و file upload و file download.

</details>

<a id="rich-mode"></a>

## طرفية وسائط Rich

تصفح شجرة الملفات، وافتح الملفات، وحرر المستندات، واعرض الوسائط داخل الطرفية.

<p align="center">
  <img src="../assets/readme_v7/rich-workbench.png" alt="منضدة CCB rich media مع معاينة Yazi داخل WezTerm" width="860">
</p>

```bash
ccb update rich
```

بعد تفعيل rich mode، يفتح `ccb` العادي rich WezTerm launcher تلقائيا ما لم يكن يعمل بالفعل داخل جلسة rich WezTerm مدارة بواسطة CCB. شغّل `ccb uninstall rich` للعودة إلى تشغيل الطرفية العادي.

<a id="agent-roles"></a>

## Agent Roles Spec وكتالوج الأدوار

يدعم CCB [Agent Roles Spec](https://github.com/SeemSeam/agent-roles-spec)، وهي مواصفة host-neutral لتغليف agents متخصصة. يمكنها جمع skills والذاكرة واعتمادات الأدوات في Role Packs قابلة للتثبيت والتركيب والإزالة. يعمل ذلك المستودع أيضا ككتالوج أدوار عام.

| Role | الغرض |
| :--- | :--- |
| `agentroles.ccb_self` | صيانة CCB الذاتية، مساعدة الإعداد، تشخيص runtime، الاسترداد المحمي، وتنسيق workflow. |
| `agentroles.archi` | مراجعة المعمارية، فحص الحدود، تحليل الترابط، مخاطر القابلية للصيانة، ونصائح gate لاحقة. |
| `agentroles.frontend_engineer` | تصميم وتنفيذ frontend، design systems، إمكانية الوصول، browser QA، وتفويض AGY المراجع. |
| `agentroles.mobile_app_engineer` | تصميم وتنفيذ mobile لـ iOS و Android و React Native و Expo و Flutter و SwiftUI و Jetpack Compose وغيرها. |
| `agentroles.mother` | إنشاء الأدوار، تدقيق role source، أبحاث الأدوار، تصميم blueprint، وفحوص توافق Agent Roles. |
| `agentroles.su_ccb` | عمليات SU-CCB workflow لتحليل المتطلبات، التخطيط، dispatch، review gates، الأرشفة، والاسترداد. |

<a id="config-memory"></a>

## الإعداد والذاكرة المشتركة

للإعداد العادي للمشروع استخدم لوحة **⚙ الإعدادات**. وإذا أردت إعدادا بمساعدة agent وتشخيص runtime، يبقى `ccb_self` متاحا كـ Role Pack اختياري ويمكن إضافته عبر `ccb roles add agentroles.ccb_self:codex`.

`.ccb/ccb_memory.md` هو مستند الذاكرة المشتركة على مستوى المشروع. استخدمه لقواعد تعاون الفريق، وقيود المشروع، والسياق طويل العمر، واتفاقيات تسليم agents. المعلومات المستقرة عبر agents يجب أن توضع هناك بدلا من نسخها في عدة ملفات ذاكرة خاصة بالـ providers.

<a id="contact"></a>

## التواصل

- Email: `bfly123@126.com`
- [Telegram group & contact / TG 群与联系](https://t.me/+BKn03v8I_ehmYzRk)
- WeChat: `seemseam-com`

<p align="center">
  <img src="../assets/weixin.png?v=0a86422d" alt="مجموعة WeChat" width="240">
</p>

<a id="community"></a>

## المجتمع والشكر

شكرا لـ [مجتمع Linux.do](https://linux.do) على الاختبار والتغذية الراجعة والنقاش.

شكرا لـ [tmux-agent-sidebar](https://github.com/hiroppy/tmux-agent-sidebar) على أفكار sidebar والإلهام.

<a id="release-notes"></a>

## ملاحظات الإصدار

<details open>
<summary><b>v8.4.0</b> - Mobile Relay مشفر واقتران مبسط وهوية مشروع ثابتة وإعادة اتصال Codex</summary>

- يضيف Relay مشفراً من طرف إلى طرف ودعوات أحادية الاستخدام وتدفقات متعددة ووضعاً رسمياً أو مستضافاً ذاتياً.
- ينقل اختيار Tailscale أو LAN الخاص أو Relay إلى `ccb update mobile`، بينما يكتفي الهاتف بمسح QR أو إدخال رمز.
- يتحقق من بيانات GitHub الرسمية والحجم وSHA-256 قبل تسليم APK الموقّع إلى Android.
- يحافظ على هوية المشروع بعد نقله ويتبع سمة النظام ويدمج reconnect اختيارياً ومحدوداً لـ Codex.

</details>

<details>
<summary><b>v8.3.1</b> - تحديثات Provider موحدة وتنظيف آمن للذاكرة المؤقتة ووصول ثابت إلى Config UI</summary>

- يوحّد ترقيات Provider المدعومة داخل `ccb update` مع فحص الإصدار وخيارات الرفض أو تخطي الإصدار المحدد، من دون إعادة تشغيل panes النشطة.
- يلغي ذاكرات Claude وGemini المؤقتة الخاصة بكل مشروع ويضيف تنظيفاً محدوداً يحافظ على المشاريع النشطة وبيانات الجلسات والمصادقة.
- يضيف منفذ loopback ثابتاً ومصدر token محمياً لـ Config UI من دون إظهار قيمة token.
- يحافظ على shutdown finalizers أثناء توقف الخادم ويستخدم تخطيط Yazi مدمجاً بعمودين في Rich mode.
- يزامن CLI وnpm وLinux وmacOS وAndroid وجميع أصول الإصدار مع 8.3.1.

</details>

<details>
<summary><b>v8.3.0</b> - دورات provider دقيقة وسلامة jobs وMobile terminal داخل المشروع</summary>

- يربط Kimi وClaude وQoder بعقود turn وactivation وsession وcompletion الأصلية لكل منها.
- يضيف follow-up للـ active job الدقيق ومراحل تنفيذ مترابطة وتشخيص inbound اليتيم ونتيجة cancellation نهائية.
- يرث امتدادات providers وإضافات Copilot مع حماية ownership صريحة للـ assets المسقطة.
- يفوض ترقيات التثبيت المُدار عبر npm إلى npm ويسحب worktrees التي تحتوي markers فقط بصورة متحفظة.
- يبقي chat وterminal في Mobile داخل workspace المشروع المحدد ويزامن جميع أسطح الإصدار مع 8.3.0.

</details>

<details>
<summary><b>v8.2.1</b> - بدء حتمي واستعادة مصادقة قابلة للتنفيذ واتصال Android في الخلفية</summary>

- أضيفت حواجز جيل البدء وإثبات الجاهزية المحدود وتشخيصات العمليات والخط الزمني.
- تتوقف حلقات إعادة التشغيل عند فشل مصادقة المزوّد نهائيًا وتظهر خطوة تسجيل الدخول المطلوبة.
- أضيف تحكم اختياري لاتصال Android في الخلفية مع حالة رد واحدة قيد التنفيذ لكل Agent.
- تمت مزامنة حزم Linux وmacOS وnpm وAndroid الموقعة مع 8.2.1.

</details>

<details>
<summary><b>v8.2.0</b> - بدء أسرع وإصلاحات للمزوّدين وموثوقية Mobile</summary>

- يقلل العمل المتكرر في بدء ccbd مع الإبقاء على فحوص lifecycle وownership.
- يصلح تعارض Grok fullscreen، ويحافظ على نوع اعتماد Claude، ويثبت اختيارات model/thinking، ويقوي تسليم Codex ask ومعالجة تأكيدات الرد.
- يحسن استعادة Mobile والمحادثة والـterminal والمرفقات والتنزيل وFCM، مع مزامنة حزم Linux وmacOS وnpm وAndroid الموقعة إلى 8.2.0.

</details>

<details open>
<summary><b>v8.0.14</b> - ترتيب دليل README ومزامنة سطح إصدار الهاتف</summary>

- عاد `README.md` في الجذر ليكون صفحة GitHub الإنجليزية.
- أصبحت ملفات README المترجمة داخل [`README/`](./)، والنسخة الصينية في [`zh.md`](zh.md).
- أصبحت روابط Mobile App و package metadata و release notes تشير إلى APK الإصدار 8.0.14.

</details>

<details>
<summary><b>v8.0.12</b> - قابلية نقل Release CI وتعريب README متعدد اللغات</summary>

- اختبارات mobile host registry تضع الآن Unix sockets المؤقتة تحت مسار قصير `/tmp/ccb-sock-*` لتجنب فشل `AF_UNIX path too long` في macOS CI.
- أصبحت `ccb update mobile` وروابط README و package metadata و mobile release manifest تشير إلى APK الإصدار 8.0.12.
- قدم v8.0.12 مجموعة README متعددة اللغات ببنية أقسام مشتركة؛ وتوجد الملفات المترجمة الحالية داخل دليل `README/`.

</details>

<details>
<summary><b>v8.0.0</b> - إصدار CCB Mobile Monorepo</summary>

- دخل مصدر Flutter لـ CCB Mobile رسميا إلى هذا المستودع، مع نشر Android APK عبر GitHub Releases.
- أضيف اكتشاف مشاريع mobile على مستوى الخادم، و pairing، و authenticated gateway routes، و pane-native message input، و conversation context rendering، و terminal access، ورفع/تنزيل الصور والمستندات.
- أصبح `ccb update mobile` مدخل Tailscale Tailnet onboarding موحدا مع إبقاء gateway على loopback-only، دون Funnel، ودون تخزين tokens أو تعديل ACLs/grants تلقائيا.

</details>

<details>
<summary><b>v7.7.0</b> - تقوية إصدار Runtime Accelerator</summary>

- تتضمن release artifacts الآن Rust `ccb-runtime-accelerator` الاختياري؛ ولم تعد agents Codex المثبتة ترجع بصمت إلى Python hot path عندما يكون sidecar متوقعا.
- عندما يجعل مسار المشروع Unix socket path طويلا جدا، ينتقل accelerator socket تلقائيا إلى per-user runtime socket root قصير.
- تم تقوية callback repair و Codex binding cache invalidation، مع أدلة مسجلة لـ regression و long-idle Codex soak و Claude callback و mixed-provider integration.

</details>

<details>
<summary><b>v7.6.19</b> - سياسة الانتظار الافتراضية لـ ask طويل المدة</summary>

- تستمر استدعاءات `ask` الطويلة العادية في انتظار نتائج provider/completion الحقيقية بدلا من الانتهاء كـ `incomplete/heartbeat_timeout` بسبب تشخيص heartbeat فقط.
- أصبحت no-terminal timeouts pane-backed في Codex و Claude و Gemini opt-in صريحة افتراضيا، مع بقاء سياسات reliability timeout الصريحة متاحة.
- أكد smoke لاختبار source-runtime ask لمدة 32 دقيقة أن المهمة يمكن أن تبقى running لأكثر من 30 دقيقة ثم تكتمل بـ `result_message` دون دليل `heartbeat_timeout` أو `incomplete`.

</details>

راجع التاريخ الكامل في [CHANGELOG.md](../CHANGELOG.md).
