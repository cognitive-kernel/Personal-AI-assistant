"""Built-in learning catalog for the personal assistant.

This is a curriculum index, not a claim that lessons are already learned or verified.
Topics are taught on demand and should be backed by current, version-aware sources.
"""
from __future__ import annotations

from typing import Any


CURRICULA: dict[str, dict[str, Any]] = {
    "artificial-intelligence": {
        "title": "هوش مصنوعی و یادگیری ماشین",
        "category": "AI",
        "prerequisites": ["Python", "ریاضی پایه", "آمار"],
        "topics": [
            "مبانی هوش مصنوعی و عامل‌های هوشمند", "منطق، جست‌وجو و حل مسئله",
            "احتمال، آمار و جبر خطی برای AI", "ساختمان داده و الگوریتم",
            "آماده‌سازی داده و مهندسی داده", "یادگیری نظارت‌شده و بدون نظارت",
            "ارزیابی مدل، اعتبارسنجی و جلوگیری از نشت داده", "شبکه‌های عصبی و backpropagation",
            "PyTorch و آموزش مدل", "Transformer و مدل‌های زبانی بزرگ",
            "Embedding، جست‌وجوی برداری و RAG", "Prompt و context engineering",
            "عامل‌ها، ابزارها و خروجی ساختاریافته", "حافظه و گراف دانش",
            "Fine-tuning، LoRA و ارزیابی", "ایمنی، حریم خصوصی و امنیت AI",
            "استقرار محلی مدل‌ها و مدیریت منابع", "پروژه نهایی قابل ارزیابی",
        ],
    },
    "python": {
        "title": "Python از مقدماتی تا حرفه‌ای",
        "category": "Programming",
        "prerequisites": [],
        "topics": [
            "نصب، محیط مجازی و اجرای برنامه", "نحو، متغیرها و انواع داده",
            "شرط‌ها، حلقه‌ها و کنترل جریان", "توابع، scope و decorator",
            "لیست، tuple، set، dict و comprehension", "ماژول‌ها، package و dependency",
            "خطاها، exception و طراحی خطا", "فایل، مسیر، JSON و CSV",
            "برنامه‌نویسی شی‌گرا و composition", "type hints و static analysis",
            "pytest، تست واحد و regression", "asyncio، thread و process",
            "SQLite، SQL و الگوهای دسترسی داده", "HTTP، API و FastAPI",
            "ساخت package و wheel", "امنیت، logging و مدیریت secret",
            "profiling، حافظه و کارایی", "خواندن کد پروژه و رفع باگ",
            "استقرار، مشاهده‌پذیری و پروژه نهایی",
        ],
    },
    "rust": {
        "title": "Rust",
        "category": "Programming",
        "prerequisites": ["مبانی برنامه‌نویسی"],
        "topics": [
            "Toolchain، Cargo و نحو پایه", "Ownership و move semantics",
            "Borrowing، reference و lifetime", "struct، enum و pattern matching",
            "collection، iterator و closure", "Result، Option و مدیریت خطا",
            "generic، trait و trait object", "module، crate و workspace",
            "smart pointer و interior mutability", "thread، channel و هم‌زمانی امن",
            "async Rust و cancellation", "تست، benchmark و profiling",
            "unsafe، FFI و برنامه‌نویسی سیستمی", "شبکه، دیتابیس و سرویس",
            "امنیت وابستگی‌ها و پروژه نهایی",
        ],
    },
    "c": {
        "title": "زبان C و برنامه‌نویسی سیستمی",
        "category": "Programming",
        "prerequisites": ["مبانی برنامه‌نویسی"],
        "topics": [
            "کامپایل، linker و ساختار برنامه", "نوع‌ها، expression و کنترل جریان",
            "آرایه، رشته و کتابخانه استاندارد", "pointer و pointer arithmetic",
            "مدیریت حافظه و طول عمر داده", "struct، enum و union",
            "فایل، I/O و داده باینری", "header، build و Make",
            "debugger، sanitizer و تست", "process، signal و file descriptor",
            "thread، synchronization و race condition", "socket و شبکه",
            "undefined behavior و امنیت حافظه", "پروژه نهایی سیستمی",
        ],
    },
    "php-web": {
        "title": "PHP و توسعه وب",
        "category": "Programming",
        "prerequisites": ["HTML", "CSS", "JavaScript", "HTTP"],
        "topics": [
            "نحو PHP و اجرای CLI/Web", "تابع، آرایه و مدیریت خطا",
            "OOP، interface و namespace", "Composer، PSR و dependency management",
            "HTTP، routing، form و session", "SQL و اتصال امن به دیتابیس",
            "MVC، service و repository", "Laravel و معماری framework",
            "REST API، validation و versioning", "authentication و authorization",
            "امنیت وب: XSS، SQL injection، CSRF و SSRF", "PHPUnit و static analysis",
            "Nginx/Apache، PHP-FPM و استقرار", "کارایی، cache و queue",
            "آزمایش امنیتی مجاز در آزمایشگاه محلی", "پروژه نهایی امن",
        ],
    },
    "javascript-web": {
        "title": "JavaScript و توسعه وب",
        "category": "Programming",
        "prerequisites": ["HTML", "CSS"],
        "topics": [
            "نحو، type، function و scope", "object، array و destructuring",
            "DOM، event و فرم‌ها", "ES modules و مدیریت package",
            "Promise، async/await و event loop", "fetch، HTTP و Web API",
            "Node.js و ساخت سرویس", "مبانی TypeScript",
            "frontend architecture و مدیریت state", "تست واحد و مرورگر",
            "امنیت XSS، CSP و مدیریت ورودی", "performance و profiling",
            "observability، build و deployment", "پروژه نهایی وب",
        ],
    },
    "sql-server": {
        "title": "Microsoft SQL Server",
        "category": "Database",
        "prerequisites": ["مبانی SQL"],
        "reference_urls": [
            {"title": "W3Schools — SQL Tutorial", "url": "https://www.w3schools.com/sql/default.asp",
             "description": "مرجع مفاهیم عمومی SQL؛ برای معماری و قابلیت‌های اختصاصی SQL Server به مستندات Microsoft نیز مراجعه کنید."},
            {"title": "Microsoft Learn — SQL Server", "url": "https://learn.microsoft.com/en-us/sql/sql-server/",
             "description": "مستندات رسمی محصول برای رفتارها و قابلیت‌های اختصاصی SQL Server."},
        ],
        "topics": [
            "معماری SQL Server، Database Engine و ابزارهای مدیریت", "T-SQL، عملیات CRUD و مدیریت خطاهای query",
            "JOIN، subquery، CTE و window function", "مدل‌سازی رابطه‌ای، کلید و constraint",
            "ایندکس، آمار و execution plan", "transaction، isolation level و lock",
            "stored procedure، function و trigger", "login، user، role و least privilege",
            "backup، restore و بازیابی نقطه‌ای", "Query Store، DMV و Extended Events",
            "بهینه‌سازی query و رفع deadlock", "High Availability و Disaster Recovery",
            "ارتقا، migration و سازگاری نسخه", "پروژه نهایی DBA و performance",
        ],
    },
    "mysql": {
        "title": "MySQL",
        "category": "Database",
        "prerequisites": ["مبانی SQL"],
        "topics": [
            "معماری سرور و ابزارهای اتصال", "SQL پایه و joinها",
            "مدل‌سازی داده و constraint", "index و optimizer",
            "InnoDB، MVCC و buffer pool", "transaction، lock و deadlock",
            "view، procedure، function و trigger", "امنیت حساب‌ها و TLS",
            "backup، restore و recovery", "replication و دسترس‌پذیری بالا",
            "EXPLAIN و تحلیل query کند", "پایش و عیب‌یابی production",
            "پروژه نهایی مدیریت و بهینه‌سازی دیتابیس",
        ],
    },
    "sqlite": {
        "title": "SQLite",
        "category": "Database",
        "prerequisites": ["مبانی SQL"],
        "topics": [
            "معماری embedded و ابزار CLI", "SQL، join و aggregation",
            "رابطه‌ها، constraint و normalization", "index و query planner",
            "transaction، journal و WAL", "CTE، window function، JSON و FTS",
            "migration نسخه‌بندی‌شده", "concurrency و دسترسی چندپردازه‌ای",
            "backup، integrity check و recovery", "امنیت فایل و بارگذاری extension",
            "بهینه‌سازی و benchmark", "پروژه نهایی دیتابیس محلی",
        ],
    },
    "android": {
        "title": "توسعه Android",
        "category": "Mobile",
        "prerequisites": ["Kotlin", "مبانی برنامه‌نویسی"],
        "topics": [
            "Android SDK، Gradle و Android Studio", "Kotlin و null safety",
            "Jetpack Compose و state", "lifecycle و معماری اپ",
            "navigation و طراحی UI", "HTTP API و serialization",
            "Room، SQLite و migration", "coroutines و Flow",
            "permissions و امنیت داده", "تست واحد و UI",
            "performance، memory و battery", "signing، build و انتشار",
            "پروژه نهایی Android",
        ],
    },
    "ios": {
        "title": "توسعه iOS",
        "category": "Mobile",
        "prerequisites": ["Swift", "مبانی برنامه‌نویسی"],
        "topics": [
            "iOS SDK، Xcode و simulator", "Swift و type system",
            "SwiftUI، view و state", "lifecycle و معماری اپ",
            "navigation و data flow", "URLSession و API",
            "Core Data، SQLite و persistence", "concurrency و async/await",
            "Keychain، privacy و permissions", "تست و debugging",
            "performance و memory", "signing، archive و release",
            "پروژه نهایی iOS",
        ],
    },
    "pentest": {
        "title": "امنیت و آزمون نفوذ مجاز",
        "category": "Security",
        "prerequisites": ["Linux", "شبکه", "HTTP"],
        "topics": [
            "مبانی امنیت، threat modeling و trust boundary", "Linux و شبکه برای امنیت",
            "HTTP، TLS و مدیریت session", "OWASP و امنیت برنامه وب",
            "شناسایی دارایی در محیط مجاز", "ارزیابی آسیب‌پذیری و false positive",
            "آزمون XSS، SQL injection و CSRF در آزمایشگاه", "امنیت API و کنترل دسترسی",
            "بازبینی امن کد و dependencyها", "تحلیل ترافیک و ابزارهای آزمایشگاهی",
            "گزارش‌نویسی، اصلاح و retest", "آزمایشگاه محلی آسیب‌پذیر",
        ],
    },
    "cisco-networking": {
        "title": "شبکه و تجهیزات Cisco",
        "category": "Networking",
        "prerequisites": ["مبانی شبکه"],
        "topics": [
            "OSI، TCP/IP و Ethernet", "IPv4، IPv6، subnetting و VLSM",
            "Cisco IOS و CLI", "switching، MAC table و VLAN",
            "trunk، 802.1Q و inter-VLAN routing", "STP، RSTP و EtherChannel",
            "static route و routing table", "OSPF و مفاهیم BGP/EIGRP",
            "DHCP، DNS، NAT و PAT", "ACL و امن‌سازی device",
            "VPN، wireless و QoS", "SNMP، Syslog و مانیتورینگ",
            "packet analysis و عیب‌یابی", "automation با Python/API",
            "پروژه نهایی شبکه سازمانی",
        ],
    },
    "forex-metatrader": {
        "title": "بازار Forex و MetaTrader 4/5",
        "category": "Trading Technology",
        "prerequisites": ["مبانی بازار مالی", "آمار پایه"],
        "topics": [
            "ساختار بازار Forex و جفت‌ارزها", "pip، lot، spread، commission و swap",
            "leverage، margin و ریسک", "نوع سفارش‌ها و محدودیت اجرا",
            "سشن‌های بازار و رویدادهای اقتصادی", "تحلیل کندل، روند و ساختار بازار",
            "اندیکاتورها و محدودیت سیگنال‌ها", "مدیریت ریسک و اندازه موقعیت",
            "آمار معاملات، drawdown و expectancy", "بک‌تست و کنترل bias",
            "MetaTrader 4/5 و اتصال امن داده", "MQL4/MQL5 و ساخت indicator",
            "ساخت Expert Advisor و مدیریت سفارش", "آزمون strategy در demo و داده تاریخی",
            "پروژه نهایی با گزارش ریسک و نتایج",
        ],
    },
    "mcse-windows-server": {
        "title": "Windows Server و مسیر آموزشی MCSE",
        "category": "Systems Administration",
        "prerequisites": ["آشنایی مقدماتی با رایانه و سخت‌افزار", "مبانی Windows", "مبانی شبکه"],
        "topics": [
            "معماری Windows Server و نصب در آزمایشگاه", "مدیریت Server Core و ابزارهای مدیریتی",
            "Active Directory Domain Services و domain design", "DNS در محیط دامنه",
            "DHCP، IPAM و مدیریت آدرس", "Organizational Unit، user، group و delegation",
            "Group Policy و مدیریت پیکربندی", "File Server، NTFS و share permissions",
            "Certificate Services و PKI", "Hyper-V و مجازی‌سازی Windows",
            "PowerShell برای مدیریت و اتوماسیون", "Windows Defender، auditing و hardening",
            "WSUS و مدیریت به‌روزرسانی", "Failover clustering و سرویس‌های در دسترس‌پذیر",
            "Backup، restore و disaster recovery", "مانیتورینگ، performance و troubleshooting",
            "مهاجرت سرویس‌ها و سازگاری نسخه", "پروژه نهایی دامنه سازمانی",
        ],
        "notes": "MCSE نام یک مسیر/گواهی قدیمی است؛ سرفصل‌ها باید بر اساس نسخه پشتیبانی‌شده Windows Server به‌روز شوند و به‌معنی فعال‌بودن گواهی قدیمی نیستند.",
    },
    "mikrotik-routeros": {
        "title": "MikroTik RouterOS و مدیریت شبکه",
        "category": "Networking",
        "prerequisites": ["مبانی رایانه و شبکه", "TCP/IP مقدماتی", "مفاهیم پایه routing و switching"],
        "topics": [
            "RouterOS، RouterBOARD و آزمایشگاه امن", "WinBox، WebFig، CLI و مدیریت دسترسی",
            "Bridge، interface و VLAN", "IPv4/IPv6، subnetting و address list",
            "DHCP، DNS و NTP", "static route و policy routing",
            "OSPF و مفاهیم BGP بر اساس مدل دستگاه", "NAT، masquerade و port forwarding",
            "Firewall filter، raw و connection tracking", "WireGuard، IPsec و VPN",
            "Wireless، امنیت Wi-Fi و لینک‌های رادیویی", "QoS، queues و bandwidth shaping",
            "monitoring، logging و packet capture", "backup، export و بازیابی تنظیمات",
            "script، scheduler و automation", "امن‌سازی management plane و به‌روزرسانی",
            "عیب‌یابی و طراحی شبکه چندبخشی", "پروژه نهایی RouterOS با مستندات",
        ],
    },
    "vmware-esxi": {
        "title": "VMware ESXi و vSphere",
        "category": "Virtualization",
        "prerequisites": ["مبانی رایانه و سخت‌افزار سرور", "مبانی سیستم‌عامل", "مبانی شبکه", "مبانی ذخیره‌سازی"],
        "topics": [
            "مفاهیم hypervisor و معماری ESXi/vSphere", "سازگاری سخت‌افزار و نصب ESXi در آزمایشگاه",
            "ESXi Host Client و مدیریت host", "ساخت VM، virtual hardware و guest tools",
            "vSwitch، port group، VLAN و شبکه مجازی", "datastore، VMFS، NFS و storage path",
            "vCenter و مدیریت چند host", "template، clone و lifecycle ماشین مجازی",
            "snapshot، محدودیت‌ها و روش backup صحیح", "vMotion و Storage vMotion",
            "vSphere HA، DRS و resource pools", "مجوزها، نقش‌ها و مدیریت هویت",
            "امنیت host، patching و مدیریت چرخه عمر", "monitoring، logs و performance",
            "PowerCLI و اتوماسیون", "backup، restore و disaster recovery",
            "عیب‌یابی شبکه، storage و VM", "پروژه نهایی خوشه مجازی‌سازی",
        ],
        "notes": "نام محصول و قابلیت‌ها به نسخه و مجوز VMware/Broadcom وابسته‌اند؛ هر درس باید نسخه هدف را مشخص کند.",
    },
}


# Load the full technical curricula before serving the learning catalog.
from .curricula_expansion import extend_curricula

extend_curricula(CURRICULA)

ALIASES = {
    "ai": "artificial-intelligence", "machine learning": "artificial-intelligence",
    "هوش مصنوعی": "artificial-intelligence", "یادگیری ماشین": "artificial-intelligence",
    "py": "python", "پایتون": "python",
    "cisco": "cisco-networking", "ccna": "cisco-networking", "سیسکو": "cisco-networking",
    "forex": "forex-metatrader", "mt4": "forex-metatrader", "mt5": "forex-metatrader",
    "mql4": "forex-metatrader", "mql5": "forex-metatrader", "متاتریدر": "forex-metatrader",
    "mcse": "mcse-windows-server", "windows server": "mcse-windows-server",
    "mikrotik": "mikrotik-routeros", "routeros": "mikrotik-routeros", "میکروتیک": "mikrotik-routeros",
    "esx": "vmware-esxi", "esxi": "vmware-esxi", "vmware": "vmware-esxi", "vsphere": "vmware-esxi",
}


def _learning_levels(topics: list[str]) -> list[dict[str, Any]]:
    """Expose every course as an ordered foundation-to-advanced learning path."""
    count = len(topics)
    first = (count + 2) // 3
    second = (count - first + 1) // 2
    return [
        {"level": "beginner", "title": "پایه", "topics": list(topics[:first])},
        {"level": "intermediate", "title": "میانی", "topics": list(topics[first:first + second])},
        {"level": "advanced", "title": "پیشرفته", "topics": list(topics[first + second:])},
    ]


def list_curricula() -> list[dict[str, Any]]:
    result = []
    for slug, item in CURRICULA.items():
        result.append({
            "slug": slug,
            "title": item["title"],
            "category": item["category"],
            "prerequisites": list(item.get("prerequisites", [])),
            "topic_count": len(item["topics"]),
            "levels": [
                {"level": level["level"], "title": level["title"], "topic_count": len(level["topics"])}
                for level in _learning_levels(item["topics"])
            ],
            "notes": item.get("notes"),
            "reference_urls": list(item.get("reference_urls", [])),
        })
    return result


def get_curriculum(slug: str) -> dict[str, Any] | None:
    key = slug.strip().lower()
    key = ALIASES.get(key, key)
    item = CURRICULA.get(key)
    if item is None:
        return None
    topics = list(item["topics"])
    return {"slug": key, **item.copy(), "topics": topics,
            "levels": _learning_levels(topics),
            "prerequisites": list(item.get("prerequisites", []))}
