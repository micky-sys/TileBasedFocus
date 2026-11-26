# ניסוי רעידות — Teensy / X-Parallax

עודכן: 26 בנובמבר 2026.

תיקיית העבודה הראשית של הניסוי:

`G:\My Drive\Micky\WORK\Cursor\TileBasedFocus\Vibration testing`

הפרויקט כולל איסוף נתוני תאוצה ומהירות זוויתית מחיישני ICM-42688-P באמצעות Teensy 4.1, שמירה לכרטיס SD, הורדת ההקלטות למחשב ותכנון מיקום החיישנים במערכת Parallax.

## נקודת ההתחלה לניסוי הנוכחי

- קושחה: [DualCsvLogger 2.2](firmware/DualCsvLogger/README.md), לשני חיישנים המחוברים ל־SEN1/J1 ול־SEN3/J3.
- תוכנה למחשב: [Vibration Monitor 2.2](VibrationMonitor/release/2.2/VibrationMonitor.exe). [הוראות שימוש](VibrationMonitor/README.md).
- הקלטות קיימות: [data/recordings](data/recordings) — שמונה קובצי CSV שהועברו מ־Documents/XParallaxLogs, בשמותיהם המקוריים.
- הגדרת תיקיית השמירה של Vibration Monitor במחשב זה עודכנה ל־`data/recordings` בפרויקט. במחשב אחר יש לבחור תיקייה זו כאשר התוכנה מנותקת מה־Teensy; ברירת המחדל המובנית בתוכנה עדיין היא `Documents/XParallaxLogs`.

לפי התיעוד הקיים, גרסה 2.1 השלימה ניסוי שולחני של 18.66 שניות ללא overflow. בגרסה 2.2 נוספה התאמת צירי הזמן בעזרת FSYNC; התיעוד מציין שעדיין נדרש אימות פיזי לגרסה זו. היא אינה כוללת קליטת trigger/strobe של המצלמות. העברת הקבצים אינה משנה את סטטוס האימות הזה.

## מפת הקבצים

- [firmware](firmware) — כל קושחות ה־Teensy, קוד מקור, בדיקות, כלי פענוח, דפי נתונים, חבילות Arduino וקובצי HEX. נשמרו גם כלי הבנייה והתוצרים הקיימים.
- [VibrationMonitor](VibrationMonitor) — קוד ותוכנת Windows להורדת CSV ולהצגת נתוני החיישנים; נשמרו גם גרסאות קודמות.
- [FlightBridge](FlightBridge/README.md) — תוכנת GPS/RS-232 ושליטה בקושחת ארבעת החיישנים. שייכת למסלול העבודה של VibrationLogger.
- [docs/vibration_test](docs/vibration_test/vibration_test_brief.md) — מסמך הניסוי והדיון הקיים, מפרט הרכזת, הצעות הצבה ותמונות המערכת.
- [analysis/sensor-placement](analysis/sensor-placement/sensor_placement_cad_review.md) — ניתוח מיקומי החיישנים על בסיס CAD, תמונות ונתוני מיקום.
- [CAD model](CAD%20model) — מודלי STEP ו־Parasolid ששימשו לבדיקת ההצבה.
- [hardware/gerbers](hardware/gerbers) — קובצי Gerber מקוריים של החיישן והרכזת, כולל גרסאות קודמות.
- [hardware/models](hardware/models) — מודלי לוח החיישן ומרווח הרכזת.
- [hardware/reference](hardware/reference) — חומרי עזר מכניים ותמונות ייחוס.
- [tools/TeensyLoader](tools/TeensyLoader) — עותק כלי Teensy Loader שהיה בתיקיית ההורדות.
- [tmp](tmp) — חומרי העבודה ההיסטוריים: סקירות Gerber, בדיקות חיבורים, עיבוד CAD, תמונות ותלויות Python. השם נשמר כדי שסקריפטים קיימים ימשיכו למצוא את קובציהם; זו אינה הוראה למחוק את התיקייה.
- [migration/2026-11-26](migration/2026-11-26) — רישום ההעברה, מיפוי המקורות והיעדים וערכי SHA-256 לאימות.

## עבודה ובנייה מהמיקום החדש

קובצי ה־README של כל רכיב הם התיעוד המפורט שלו. קושחת ארבעת החיישנים מתועדת ב־[firmware/README.md](firmware/README.md); כלי בדיקת הרכזת ב־[HubVitality](firmware/HubVitality/README.md); מסלול חיישן יחיד ב־[ResetCsvLogger](firmware/ResetCsvLogger/README.md).

תצורת Arduino היא מקומית למחשב. במהלך ההעברה התקבל ביעד עדכון חיצוני שמפנה ל־`C:/Users/Simplex/...`; הוא נשמר, וגרסת המקור נשמרה בדוח ההעברה. לפני בניית קושחה במחשב אחר או מהמיקום החדש, יש להריץ תחילה `firmware/build.ps1` כדי ליצור מחדש את התצורה בהתאם למיקום הנוכחי. לבניית תוכנות Windows, הסקריפטים `VibrationMonitor/build.ps1` ו־`FlightBridge/build.ps1` משתמשים בתיקיית בנייה מקומית תחת `%LOCALAPPDATA%\XParallaxBuild`, ולאחר מכן מעתיקים את קובץ ההפעלה לתיקיית `release` ובודקים את שלמותו. כך נשמרת הנחיית הפרויקט לבנות את קובצי ההפעלה על כונן מקומי.

סקריפטי ה־CAD שמסתמכים על `CAD model` יש להריץ משורש פרויקט זה. חלק מקובצי הביניים והמסמכים ההיסטוריים מכילים נתיבים ישנים; אלה נשמרו כחלק מהיסטוריית העבודה. בבנייה מחדש יש להשתמש בסקריפטים, ולא בפקודות ישנות מתוך קובצי הביניים.

## ניהול הקבצים

יש לשמור הקלטות מקוריות ב־`data/recordings`, ואת תוצאות העיבוד בתיקיות נפרדות תחת `analysis`. קובצי `tests/demo.csv` ו־`tests/sync-demo.csv` הם נתוני בדיקה מלאכותיים, ואינם מדידות הניסוי.

גרסאות ותיעוד קודמים נשמרו ללא מיזוג או דריסה. בסיום שינוי יש לעדכן את התיעוד של הרכיב הרלוונטי ואת README זה אם מבנה הפרויקט או אופן ההפעלה השתנו. פירוט המעבר ושינויי הנתיבים נמצא ב־[דוח ההעברה](migration/2026-11-26/MIGRATION.md).

בזמן ההעברה התקבלה ביעד גם בנייה חדשה של DualCsvLogger. היא נשמרה בתיקיית הקושחה הפעילה, ותוצרי הבנייה המקוריים הועברו ל־`migration/2026-11-26/original-builds/build-dualcsvlogger`.

## GitHub

הפרויקט נמצא בתוך המאגר [micky-sys/TileBasedFocus](https://github.com/micky-sys/TileBasedFocus), בתיקיית `Vibration testing`. המאגר ציבורי.

ניהול הגרסאות כולל את קוד המקור, הבדיקות, סקריפטי הבנייה והתיעוד. הקלטות, CAD, חומרי חומרה, כלי צד שלישי, קובצי PDF, גרסאות בינאריות ותוצרי עבודה זמניים נשמרים בתיקיית Drive ומוחרגים באמצעות `.gitignore`. גם דוח ההעברה וקובצי האימות נשמרים מקומית ב־Drive. קישורים לקבצים מוחרגים מיועדים לעותק המלא ב־Drive ואינם זמינים בשכפול של המאגר בלבד.

גם `firmware/arduino-cli.yaml` הוא קובץ הגדרות מקומי. בשכפול חדש יש להפעיל `firmware/build.ps1 -InstallToolchain` כדי להתקין את כלי Teensy וליצור את התצורה בהתאם למיקום המקומי, כמפורט ב־README של הקושחה.
