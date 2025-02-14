from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import sqlite3
import datetime
import re

app = Flask(__name__)
DB_NAME = 'diary.db'

def format_date_local(date_obj):
    """
    מחזירה מחרוזת תאריך בפורמט ישראלי (ללא אפסים מובילים): D-M-YYYY
    לדוגמה: 15-3-2025
    """
    return f"{date_obj.day}-{date_obj.month}-{date_obj.year}"

def init_db():
    """
    יוצר את מסד הנתונים והטבלאות במידה והן אינן קיימות.
    """
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            diary_name TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            entry_time TEXT,
            content TEXT NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            phone TEXT PRIMARY KEY,
            current_diary TEXT NOT NULL,
            current_date TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def is_valid_date(date_str):
    """
    בודקת אם מחרוזת התאריך תקינה לפי הפורמט הישראלי: D-M-YYYY
    לדוגמה: 15-3-2025
    """
    try:
        datetime.datetime.strptime(date_str, "%d-%m-%Y")
        return True
    except ValueError:
        return False

def get_user_context(phone):
    """
    מחזירה את הגדרות המשתמש (יומן נוכחי ותאריך נוכחי).
    אם המשתמש חדש, מגדירה את היומן ל"ברירת מחדל" והתאריך להיום.
    """
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT current_diary, current_date FROM users WHERE phone = ?", (phone,))
    row = c.fetchone()
    if row:
        diary, date_str = row
    else:
        diary = "ברירת מחדל"
        date_str = format_date_local(datetime.date.today())
        c.execute("INSERT INTO users (phone, current_diary, current_date) VALUES (?, ?, ?)",
                  (phone, diary, date_str))
        conn.commit()
    conn.close()
    return {"diary": diary, "date": date_str}

def update_user_diary(phone, diary_name):
    """
    מעדכנת את היומן הנוכחי של המשתמש.
    """
    context = get_user_context(phone)
    current_date = context['date']
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (phone, current_diary, current_date) VALUES (?, ?, ?)",
              (phone, diary_name, current_date))
    conn.commit()
    conn.close()

def update_user_date(phone, date_str):
    """
    מעדכנת את התאריך הנוכחי של המשתמש.
    """
    context = get_user_context(phone)
    current_diary = context['diary']
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (phone, current_diary, current_date) VALUES (?, ?, ?)",
              (phone, current_diary, date_str))
    conn.commit()
    conn.close()

def add_entry(phone, diary_name, entry_date, entry_time, content):
    """
    מוסיפה רשומה חדשה ליומן.
    """
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO entries (phone, diary_name, entry_date, entry_time, content) VALUES (?, ?, ?, ?, ?)",
              (phone, diary_name, entry_date, entry_time, content))
    conn.commit()
    conn.close()

def get_entries(phone, diary_name, entry_date):
    """
    מחזירה את כל הרשומות של היומן עבור תאריך מסוים.
    הרשומות מסודרות כך שרשומות עם שעה יוצגו לפי הזמן.
    """
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "SELECT id, entry_time, content FROM entries WHERE phone = ? AND diary_name = ? AND entry_date = ? ORDER BY (CASE WHEN entry_time IS NULL THEN 1 ELSE 0 END), entry_time",
        (phone, diary_name, entry_date)
    )
    rows = c.fetchall()
    conn.close()
    return rows

def delete_entry_by_index(phone, diary_name, entry_date, index):
    """
    מוחקת רשומה לפי מספר הסידורי כפי שמוצג למשתמש.
    """
    entries = get_entries(phone, diary_name, entry_date)
    if index < 1 or index > len(entries):
        return False, "מספר רשומה לא תקין."
    entry_id = entries[index-1][0]
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()
    return True, "הרשומה נמחקה בהצלחה."

def edit_entry_by_index(phone, diary_name, entry_date, index, new_content):
    """
    מעדכנת את תוכן הרשומה לפי מספר סידורי.
    """
    entries = get_entries(phone, diary_name, entry_date)
    if index < 1 or index > len(entries):
        return False, "מספר רשומה לא תקין."
    entry_id = entries[index-1][0]
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE entries SET content = ? WHERE id = ?", (new_content, entry_id))
    conn.commit()
    conn.close()
    return True, "הרשומה עודכנה בהצלחה."

def is_valid_time(time_str):
    """
    בודקת אם מחרוזת הזמן עומדת בפורמט 24 שעות: HH:MM
    לדוגמה: 09:30 או 14:00
    """
    pattern = r'^([01]\d|2[0-3]):[0-5]\d$'
    return re.match(pattern, time_str) is not None

@app.route("/whatsapp", methods=['POST'])
def whatsapp_bot():
    """
    נקודת הקצה שאליה Twilio תשלח את הודעות הוואטסאפ.
    הפונקציה מפרשת את ההודעה ומבצעת את הפעולה המתאימה בהתאם לפקודות.
    """
    incoming_msg = request.form.get('Body', '').strip()
    phone = request.form.get('From')
    response = MessagingResponse()
    msg = response.message()
    
    user_context = get_user_context(phone)
    current_diary = user_context["diary"]
    current_date = user_context["date"]
    
    tokens = incoming_msg.split()
    if not tokens:
        msg.body("לא התקבל קלט. אנא שלח פקודה. הקלד 'עזרה' למידע.")
        return str(response)
    
    command = tokens[0].lower()
    
    # פקודת עזרה: מציגה למשתמש את כל הפקודות ודוגמאות לשימוש
    if command == "עזרה":
        help_text = (
            "הוראות שימוש בבוט היומן:\n\n"
            "1. בחירת יומן:\n"
            "   - מעבר ליומן אחר: 'בחר יומן עבודה'\n"
            "   - יצירת יומן חדש: 'צור יומן לימודים'\n\n"
            "2. בחירת תאריך:\n"
            "   - לדוגמה: 'בחר תאריך 15-3-2025' (יום-חודש-שנה)\n"
            "   - ברירת המחדל היא התאריך של היום\n\n"
            "3. הוספת רשומה:\n"
            "   - עם שעה: 'הוסף 14:00 פגישה עם לקוח'\n"
            "   - בלי שעה: 'הוסף לעשות קניות'\n"
            "   (הרשומה תתווסף לתאריך הנבחר או לתאריך של היום אם לא נבחר תאריך אחר)\n\n"
            "4. הצגת רשומות:\n"
            "   - כתבו 'צפה' או 'הצג' כדי לראות את הרשומות עבור התאריך הנבחר\n\n"
            "5. הסרת רשומה:\n"
            "   - לדוגמה: 'הסר 2' כאשר 2 הוא מספר הרשומה כפי שמוצג\n\n"
            "6. עריכת רשומה:\n"
            "   - לדוגמה: 'ערוך 3 עדכון: הפגישה נדחתה'\n\n"
            "פורמטים חשובים:\n"
            "   - תאריך: D-M-YYYY (לדוגמה: 15-3-2025)\n"
            "   - שעה: HH:MM (24 שעות, לדוגמה: 09:30)\n\n"
            "בהצלחה!"
        )
        msg.body(help_text)
    
    # בחירת יומן
    elif command == "בחר" and len(tokens) >= 3 and tokens[1] == "יומן":
        diary_name = " ".join(tokens[2:])
        update_user_diary(phone, diary_name)
        msg.body(f"היומן שונה. כעת אתה עובד עם היומן: {diary_name}\nתאריך נוכחי: {current_date}")
    
    elif command == "צור" and len(tokens) >= 3 and tokens[1] == "יומן":
        diary_name = " ".join(tokens[2:])
        update_user_diary(phone, diary_name)
        msg.body(f"יומן חדש נוצר. כעת אתה עובד עם היומן: {diary_name}\nתאריך נוכחי: {current_date}")
    
    # בחירת תאריך
    elif command == "בחר" and len(tokens) >= 3 and tokens[1] == "תאריך":
        date_str = tokens[2]
        if not is_valid_date(date_str):
            msg.body("תאריך לא תקין. יש להזין בתבנית D-M-YYYY, לדוגמה: 15-3-2025.")
        else:
            update_user_date(phone, date_str)
            msg.body(f"תאריך נבחר: {date_str}\nיומן: {current_diary}")
    
    # הצגת רשומות עבור התאריך הנבחר
    elif command in ["צפה", "הצג"]:
        entries = get_entries(phone, current_diary, current_date)
        if not entries:
            msg.body(f"אין רשומות ביומן שלך עבור התאריך {current_date}.")
        else:
            response_text = f"רשומות היומן ({current_diary}) לתאריך {current_date}:\n"
            for idx, entry in enumerate(entries, start=1):
                time_str = entry[1] if entry[1] else ""
                response_text += f"{idx}. {time_str} - {entry[2]}\n"
            msg.body(response_text)
    
    # הוספת רשומה
    elif command == "הוסף" and len(tokens) >= 2:
        possible_time = tokens[1]
        if is_valid_time(possible_time):
            entry_time = possible_time
            content = " ".join(tokens[2:]).strip()
            if content == "":
                msg.body("נא להזין תוכן לרשומה.")
                return str(response)
        else:
            entry_time = None
            content = " ".join(tokens[1:]).strip()
        add_entry(phone, current_diary, current_date, entry_time, content)
        msg.body(f"הרשומה נוספה בהצלחה לתאריך {current_date}.")
    
    # הסרת רשומה
    elif command == "הסר" and len(tokens) == 2:
        try:
            index = int(tokens[1])
            success, result_msg = delete_entry_by_index(phone, current_diary, current_date, index)
            msg.body(result_msg)
        except ValueError:
            msg.body("נא להזין מספר רשומה תקין.")
    
    # עריכת רשומה
    elif command == "ערוך" and len(tokens) >= 3:
        try:
            index = int(tokens[1])
            new_content = " ".join(tokens[2:]).strip()
            success, result_msg = edit_entry_by_index(phone, current_diary, current_date, index, new_content)
            msg.body(result_msg)
        except ValueError:
            msg.body("נא להזין מספר רשומה תקין.")
    
    else:
        msg.body("פקודה לא מוכרת. הקלד 'עזרה' לקבלת מידע על הפקודות הזמינות.")
    
    return str(response)

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
