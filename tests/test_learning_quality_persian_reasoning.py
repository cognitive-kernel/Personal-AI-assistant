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
        "در SQL Server دستور SELECT ستون‌ها را از جدول برمی‌گرداند. "
        "JOIN ردیف‌های دو جدول را بر اساس شرط اتصال ترکیب می‌کند. "
        "برای مثال SELECT c.Name FROM Customers AS c JOIN Orders AS o "
        "ON c.Id = o.CustomerId نام مشتریان دارای سفارش را نمایش می‌دهد. "
        "انتخاب ستون‌های مشخص خواندن داده‌های غیرضروری را کم می‌کند. "
    ) * 5
    questions = [
        "دستور SELECT چه چیزی برمی‌گرداند؟",
        "JOIN چه کاری انجام می‌دهد؟",
        "چگونه مشتریان دارای سفارش را پیدا می‌کنیم؟",
    ]
    answers = [
        "ستون‌های انتخاب‌شده از جدول را برمی‌گرداند.",
        "ردیف‌های دو جدول را با شرط اتصال ترکیب می‌کند.",
        "با اتصال Customers به Orders روی شناسه مشتری.",
    ]
    result = evaluate_learning_value(lesson, questions, answers)
    assert result["has_explanatory_reasoning"] is False
    assert result["accepted"] is True
