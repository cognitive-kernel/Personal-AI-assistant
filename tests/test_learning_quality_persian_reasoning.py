from app.learning_quality import evaluate_learning_value


def test_persian_causal_explanation_marker_is_recognized():
    lesson = (
        "در SQL Server، دستور SELECT ستون‌های موردنیاز را از جدول می‌خواند. "
        "اگر شرط WHERE روی ستون مناسب باشد، تعداد ردیف‌های بررسی‌شده کمتر می‌شود. "
        "اما این بهبود به توزیع داده و ایندکس وابسته است. "
        "برای مثال، SELECT Name FROM Customers WHERE CustomerId = 7 یک ردیف را جست‌وجو می‌کند. "
        "در نتیجه، باید برنامه اجرا و تعداد خواندن‌ها را هم بررسی کرد. "
    ) * 5
    questions = [
        "دستور SELECT چه کاری انجام می‌دهد؟",
        "چرا شرط WHERE می‌تواند مفید باشد؟",
        "چگونه اثر پرس‌وجو را بررسی می‌کنیم؟",
    ]
    answers = [
        "ستون‌های انتخاب‌شده را از جدول برمی‌گرداند.",
        "چون می‌تواند ردیف‌های نامرتبط را فیلتر کند.",
        "با بررسی برنامه اجرا و تعداد خواندن‌ها.",
    ]
    result = evaluate_learning_value(lesson, questions, answers)
    assert result["has_explanatory_reasoning"] is True


def test_lesson_is_not_rejected_only_for_missing_explanation_marker():
    lesson = (
        "در SQL Server دستور SELECT ستون‌های موردنیاز را از جدول انتخاب می‌کند. "
        "هنگام طراحی پرس‌وجو بهتر است فقط نام، شناسه و اطلاعات لازم را درخواست کنیم. "
        "عبارت JOIN داده‌های مرتبط را از دو جدول کنار هم قرار می‌دهد و شرط ON رابطه آن‌ها را مشخص می‌کند. "
        "برای مثال، جدول Customers شناسه و نام مشتری را نگه می‌دارد؛ جدول Orders شناسه سفارش و CustomerId را ذخیره می‌کند. "
        "پرس‌وجوی SELECT c.Name, o.OrderId FROM Customers AS c JOIN Orders AS o ON c.Id = o.CustomerId "
        "نام مشتری را همراه سفارش‌های ثبت‌شده نمایش می‌دهد. "
        "کلیدهای اتصال باید از نوع سازگار باشند و نام مستعار جدول‌ها خوانایی کد را افزایش می‌دهد. "
        "حذف شرط اتصال ممکن است ترکیب ناخواسته‌ای از ردیف‌ها ایجاد کند. "
        "در پایگاه داده بزرگ، بررسی ایندکس و execution plan برای ارزیابی کارایی مفید است. "
        "همچنین باید مشخص کنیم که به INNER JOIN نیاز داریم یا LEFT JOIN، نوع اتصال مشخص می‌کند ردیف‌های بدون تطبیق حفظ شوند یا خیر. "
        "در پایان، نتیجه را با چند ردیف نمونه و تعداد مورد انتظار مقایسه می‌کنیم."
    )
    questions = [
        "دستور SELECT چه چیزی برمی‌گرداند؟",
        "JOIN چه کاری انجام می‌دهد؟",
        "تفاوت مهم LEFT JOIN چیست؟",
    ]
    answers = [
        "ستون‌های انتخاب‌شده از جدول را برمی‌گرداند.",
        "ردیف‌های دو جدول را با شرط اتصال ترکیب می‌کند.",
        "ردیف‌های سمت چپ بدون تطبیق را هم حفظ می‌کند.",
    ]
    result = evaluate_learning_value(lesson, questions, answers)
    assert result["has_explanatory_reasoning"] is False
    assert result["accepted"] is True
