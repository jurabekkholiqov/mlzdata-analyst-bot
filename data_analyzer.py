import os
import re
import io
import sys
import time
import logging

logger = logging.getLogger(__name__)
import uuid
import pandas as pd
import numpy as np
import matplotlib
# Set matplotlib backend to Agg to prevent GUI thread issues in bot
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns

# Set global plotting configurations for Uzbek/Cyrillic font support & neatness
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'Arial', 'Helvetica', 'Calibri', 'Tahoma']
plt.rcParams['axes.unicode_minus'] = False # prevent missing minus sign in fonts
from google import genai

class DataAnalyzer:
    def __init__(self, api_key: str):
        """
        Initialize the DataAnalyzer with Gemini API Key.
        """
        self.api_key = api_key
        self.client = None
        if api_key and api_key != "YOUR_GEMINI_API_KEY":
            self.client = genai.Client(api_key=api_key)
            
    def is_likely_id_column(self, col_name: str, col_series: pd.Series) -> bool:
        """
        Heuristically determines if a column represents an identifier (ID, account, phone number, etc.)
        rather than a numerical measurement.
        """
        name_lower = str(col_name).lower()
        id_keywords = ['id', 'code', 'no', 'num', 'acc', 'card', 'phone', 'tel', 'inn', 'pin', 'index', 
                       'raqam', 'kod', 'karta', 'telefon', 'pasport', 'hisob', 'shot', 'schet', 'inn', 'pinfl']
        
        # Check if column name contains identifier keywords
        # But exclude names that are clearly measurements like 'number of items', 'accrued interest', 'amount'
        exclude_measurements = ['amt', 'amount', 'sum', 'count', 'qty', 'quantity', 'duration', 'total', 
                                'summa', 'miqdor', 'narx', 'price', 'balance', 'balans', 'foiz', 'percent',
                                'interest', 'sotuv', 'sales', 'revenue']
        
        has_id_keyword = False
        for kw in id_keywords:
            if kw in name_lower:
                # Double check to prevent false positives with measurements
                is_excluded = any(ex in name_lower for ex in exclude_measurements)
                if not is_excluded:
                    has_id_keyword = True
                    break
        
        if has_id_keyword:
            return True
            
        # Check values if numeric
        if np.issubdtype(col_series.dtype, np.number):
            vals = col_series.dropna()
            if len(vals) == 0:
                return False
                
            # If values are integers and mean value is extremely large (> 1e8)
            # which is typical for accounts, cards, passports, phone numbers
            non_null_vals = vals.head(100)
            try:
                is_integer_like = all(float(v).is_integer() for v in non_null_vals)
                if is_integer_like and non_null_vals.mean() > 100000000:
                    return True
            except:
                pass
                
        return False
            
    def detect_header_row(self, df_raw: pd.DataFrame) -> int:
        """
        Heuristically finds the index of the first row that is likely the header.
        Looks for the first non-empty row with string/text columns.
        """
        if len(df_raw) == 0:
            return 0
            
        for i in range(min(15, len(df_raw))):
            row = df_raw.iloc[i]
            non_null_count = row.notna().sum()
            if non_null_count > 0:
                # If this row is followed by another populated row, it's highly likely the header
                if i + 1 < len(df_raw):
                    next_row = df_raw.iloc[i + 1]
                    if next_row.notna().sum() >= non_null_count:
                        return i
                return i
        return 0

    def load_file(self, file_path: str) -> pd.DataFrame:
        """
        Loads CSV or Excel file into a Pandas DataFrame with intelligent header detection.
        """
        ext = os.path.splitext(file_path)[1].lower()
        if ext == '.csv':
            try:
                # Preview first 20 rows to detect header index
                df_preview = pd.read_csv(file_path, header=None, nrows=20)
                header_idx = self.detect_header_row(df_preview)
                return pd.read_csv(file_path, skiprows=header_idx)
            except Exception:
                return pd.read_csv(file_path)
                
        elif ext in ['.xlsx', '.xls']:
            try:
                # Try with calamine engine first
                try:
                    df_preview = pd.read_excel(file_path, header=None, nrows=20, engine='calamine')
                    header_idx = self.detect_header_row(df_preview)
                    return pd.read_excel(file_path, skiprows=header_idx, engine='calamine')
                except Exception:
                    df_preview = pd.read_excel(file_path, header=None, nrows=20)
                    header_idx = self.detect_header_row(df_preview)
                    return pd.read_excel(file_path, skiprows=header_idx)
            except Exception:
                return pd.read_excel(file_path)
        else:
            raise ValueError("Qo'llab-quvvatlanmaydigan fayl formati. Faqat .csv yoki .xlsx yuboring.")

    def get_metadata_summary(self, df: pd.DataFrame) -> str:
        """
        Generates a summary of the DataFrame structure for the LLM.
        """
        buf = io.StringIO()
        df.info(buf=buf)
        info_str = buf.getvalue()
        
        summary = f"Jadval shakli (Shape): {df.shape[0]} qator, {df.shape[1]} ustun\n"
        summary += f"Ustunlar haqida ma'lumot (Info):\n{info_str}\n"
        summary += "Dastlabki 3 qator namunasi (Head):\n"
        summary += df.head(3).to_string() + "\n"
        
        # Missing values summary
        null_counts = df.isnull().sum()
        null_summary = null_counts[null_counts > 0]
        if not null_summary.empty:
            summary += f"\nBo'sh qiymatlar (Missing values):\n{null_summary.to_string()}\n"
        else:
            summary += "\nFaylda bo'sh (null) qiymatlar yo'q.\n"
            
        # Duplicates summary
        dup_count = df.duplicated().sum()
        summary += f"Dublikat qatorlar soni: {dup_count}\n"
        
        return summary

    def clean_data(self, df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
        """
        Intelligently cleans the data using Gemini AI if available.
        Falls back to rule-based cleaning if AI is unavailable or fails.
        """
        # Save a backup for fallback
        original_df = df.copy()
        original_shape = df.shape
        
        # Fallback rule-based cleaning logic
        def fallback_clean(df_fb: pd.DataFrame) -> tuple[pd.DataFrame, str]:
            log = []
            dup_count = df_fb.duplicated().sum()
            if dup_count > 0:
                df_fb = df_fb.drop_duplicates().reset_index(drop=True)
                log.append(f"- {dup_count} ta dublikat qatorlar olib tashlandi.")
            all_null_cols = df_fb.columns[df_fb.isnull().all()]
            if len(all_null_cols) > 0:
                df_fb = df_fb.drop(columns=all_null_cols)
                log.append(f"- Butunlay bo'sh bo'lgan ustunlar o'chirildi: {list(all_null_cols)}")
            null_counts = df_fb.isnull().sum()
            for col in df_fb.columns:
                null_count = null_counts[col]
                if null_count > 0:
                    if np.issubdtype(df_fb[col].dtype, np.number):
                        median_val = df_fb[col].median()
                        df_fb[col] = df_fb[col].fillna(median_val)
                        log.append(f"- '{col}' ustunidagi {null_count} ta bo'shliq o'rtacha (median) qiymat ({median_val}) bilan to'ldirildi.")
                    else:
                        mode_val = df_fb[col].mode()
                        fill_val = mode_val[0] if not mode_val.empty else "Noma'lum"
                        df_fb[col] = df_fb[col].fillna(fill_val)
                        log.append(f"- '{col}' ustunidagi {null_count} ta bo'shliq eng ko'p uchraydigan qiymat ('{fill_val}') bilan to'ldirildi.")
            for col in df_fb.columns:
                if df_fb[col].dtype == 'object':
                    sample = df_fb[col].dropna().head(10).astype(str)
                    date_like = False
                    for val in sample:
                        if re.match(r'^\d{4}[-\/\.]\d{2}[-\/\.]\d{2}', val) or re.match(r'^\d{2}[-\/\.]\d{2}[-\/\.]\d{4}', val):
                            date_like = True
                            break
                    if date_like:
                        try:
                            df_fb[col] = pd.to_datetime(df_fb[col], errors='ignore')
                            log.append(f"- '{col}' ustuni sana formatiga o'tkazildi.")
                        except:
                            pass
            log_str = "\n".join(log) + f"\nNatija: Jadval o'lchami {original_shape} dan {df_fb.shape} ga o'zgardi." if log else "Fayl toza holatda ekan, hech qanday o'zgarish qilinmadi."
            return df_fb, log_str

        # If Gemini client is not initialized, use fallback
        if not self.client:
            return fallback_clean(df)

        metadata = self.get_metadata_summary(df)
        
        prompt = f"""
Siz professional va biznesga yo'naltirilgan yetakchi Data Analyst (Ma'lumotlar tahlilchisi) va tozalash bo'yicha mutaxassisiz.
Foydalanuvchi yuklagan jadvalning tuzilishi (metadata) quyidagicha:
{metadata}

Vazifangiz:
Ushbu jadvaldagi bo'shliqlar (null/missing values), dublikat qatorlar va keraksiz ustunlarni DATA ANALYTICS mantiqi bo'yicha tahlil qiling va ularni oqilona tozalash uchun Python kodini yozing.

Tozalash bo'yicha yo'riqnoma:
1. Dublikat qatorlarni olib tashlang.
2. Bo'shliqlar (null qiymatlar) bor ustunlarni tahlil qiling:
   - Agar ustun nomi 'Promotion', 'Promo', 'Aksiya', 'Discount', 'Chegirma' kabi marketing/chegirmaga tegishli bo'lsa va unda bo'shliqlar bo'lsa, ularni blindly (eng ko'p uchraydigan qiymat bilan) to'ldirmang! Chunki bo'sh joy chegirma yoki aksiya qo'llanilmaganligini (ya'ni aksiyasiz sotib olinganini) anglatadi. Ularni mos ravishda 'Yo'q' (yoki 'None') yoki 0 bilan to'ldiring.
   - Boshqa raqamli (numeric) ustunlardagi bo'shliqlarni median (o'rtacha) qiymat bilan to'ldiring.
   - Matnli (categorical) ustunlarni eng ko'p uchraydigan qiymat yoki 'Noma'lum' bilan to'ldiring.
3. Keraksiz ustunlarni aniqlang:
   - Agar biror ustun 100% bo'sh bo'lsa yoki barcha satrlarda faqatgina 1 ta takrorlanuvchi qiymat bo'lsa (ya'ni ma'lumot tahlil uchun hech qanday foyda keltirmasa), uni o'chirib tashlang.
4. Sana yozilgan ustunlarni datetime formatiga o'tkazing.

Kod faqat ```python ``` bloki ichida bo'lsin.
Jadval 'df' nomi ostida yuklangan.
Chop etish:
Har doim `print()` yordamida tozalash jarayoni hisobotini chop eting. Hisobot o'zbek tilida bo'lishi va quyidagi formatda tushuntirilishi kerak:

🧹 **MA'LUMOTLARNI TOZALASH HISOBOTI:**
- [Har bir qilingan o'zgarish va uning data analytics nuqtai nazaridan biznes mantiqi (masalan: 'Promotion' ustunidagi bo'shliqlar chegirma berilmaganligi sababli 'Yo'q' qiymati bilan to'ldirildi)]

Natija: Jadval o'lchami [oldingi_shape] dan [yangi_shape] ga o'zgardi.
"""

        # Run with fallback retry on 2.5-flash
        max_retries = 3
        backoff = 2
        response_text = ""
        success = False
        
        for attempt in range(max_retries):
            try:
                response = self.client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt
                )
                response_text = response.text.strip()
                success = True
                break
            except Exception as e:
                err_msg = str(e)
                if attempt < max_retries - 1 and ("503" in err_msg or "429" in err_msg or "UNAVAILABLE" in err_msg or "RESOURCE_EXHAUSTED" in err_msg or "rate limit" in err_msg.lower()):
                    time.sleep(backoff)
                    backoff += 3
                    continue
                else:
                    break

        if not success:
            # Fallback to rule-based cleaning on API error
            return fallback_clean(df)

        try:
            # Extract python code block
            code_match = re.search(r'```python\s*(.*?)\s*```', response_text, re.DOTALL)
            code = code_match.group(1) if code_match else response_text
            
            if not code.strip() or not self._is_code_safe(code):
                return fallback_clean(df)

            # Redirect stdout to capture prints
            old_stdout = sys.stdout
            redirected_output = io.StringIO()
            sys.stdout = redirected_output
            
            # Setup execution local variables
            exec_globals = {
                'df': df,
                'pd': pd,
                'np': np,
                're': re
            }
            
            error_occurred = None
            try:
                exec(code, exec_globals)
            except Exception as e:
                error_occurred = str(e)
            finally:
                sys.stdout = old_stdout
                
            if error_occurred:
                # Fallback to rule-based cleaning on code execution error
                return fallback_clean(original_df)
                
            output_text = redirected_output.getvalue().strip()
            
            if not output_text:
                output_text = f"Ma'lumotlar muvaffaqiyatli tozalandi.\nNatija: Jadval o'lchami {original_shape} dan {df.shape} ga o'zgardi."
                
            return df, output_text

        except Exception:
            return fallback_clean(original_df)

    def _is_code_safe(self, code: str) -> bool:
        """
        Performs a basic safety check on the generated python code to prevent malicious activities.
        """
        # Block imports that can interact with the system
        blocked_keywords = [
            r'\bimport\s+os\b', r'\bimport\s+sys\b', r'\bimport\s+subprocess\b', 
            r'\bimport\s+shutil\b', r'\bimport\s+urllib\b', r'\bimport\s+requests\b', 
            r'\bfrom\s+os\b', r'\bfrom\s+sys\b', r'\bfrom\s+subprocess\b',
            r'\bopen\s*\(', r'\beval\s*\(', r'\bexec\s*\(', r'__import__',
            r'\bbuiltins\b', r'\bplatform\b', r'\bsocket\b'
        ]
        for pattern in blocked_keywords:
            if re.search(pattern, code):
                return False
        return True

    def analyze_query(self, df: pd.DataFrame, user_query: str, language: str = "uz") -> tuple[str, list[str]]:
        """
        Sends metadata and user query to Gemini, receives Python code, runs it, and returns the result text
        along with the list of paths of the generated plots (if any).
        """
        if not self.client:
            return "Tizim sozlanmagan: Iltimos, birinchi navbatda Gemini API Key kalitini o'rnating.", []
            
        metadata = self.get_metadata_summary(df)
        
        # Path where a legacy plot could be saved if the AI uses it
        plot_filename = f"plot_{uuid.uuid4().hex[:8]}.png"
        plot_path = os.path.abspath(plot_filename)
        
        prompt = f"""
================================================================
MLZDATA BOT — SYSTEM PROMPT (TO'LIQ VERSIYA)
================================================================

Sen MlzData — O'zbekiston uchun yaratilgan professional AI Data Analyst botsan.
Sening vazifang: foydalanuvchi yuborgan Excel (.xlsx) yoki CSV (.csv) fayllarini
senior data analyst darajasida tahlil qilish va aniq, ishonchli natijalar berish.

================================================================
QISM 1: SHAXSIYAT VA USLUB
================================================================

- Til: Foydalanuvchi qaysi tilda muloqot qilsa (O'zbekcha, Ruscha yoki Inglizcha) yoki qaysi tilda savol bersa, SHU TILDA javob ber.
  Hozirgi muloqot tili: {language}. Hamma tahlillar, tavsiyalar, sarlavhalar va matnlar faqat {language} tilida bo'lishi shart!
- Uslub: Professionallik + soddalik. Murakkab atamalarni tushuntir.
- Ton: Qulay, lekin ishchan. Ortiqcha tariflamasdan — ish qil.
- Emojilar: Faqat natijani ko'rsatishda ishlatish mumkin (juda ko'p emas)

================================================================
QISM 3: TAHLIL QOIDALARI — XATOSIZ ISHLASH (SENIOR DATA ANALYST STANDARTLARI)
================================================================

3.1 RAQAMLI HISOB-KITOB VA IDENTIFIKATORLAR (MUHIM!)
────────────────────────────────────────────────────
- Jadvaldagi ustunlarni tahlil qilishdan oldin ularning ma'nosini tushunib oling:
  - Nomi `id`, `code`, `no`, `num`, `acc`, `card`, `phone`, `tel`, `inn`, `raqam`, `karta`, `hisob`, `shot` bo'lgan ustunlar raqamli bo'lsa ham, ular IDENTIFIKATOR hisoblanadi.
  - **HECH QACHON identifikator ustunlarini qo'shmang (sum) yoki o'rtacha qiymatini (mean/median) hisoblamang!** Masalan, bank hisob raqamlari yoki karta raqamlarining yig'indisini hisoblash mutlaqo xatodir va ma'nosiz gigant raqamlarni (masalan, 1e16) keltirib chiqaradi.
  - Identifikator ustunlari bo'yicha so'rov bo'lganda, faqat ularning sonini sanang: takrorlanmas qiymatlar soni (`.nunique()`) yoki jami yozuvlar soni (`.count()`).
- Hisoblashdan OLDIN: ustun turini tekshiring. Raqamli ustunda matn bo'lsa, uni tozalang, keyin hisoblang.
- So'm/valyuta qiymatlarida: uchliklar bilan ajratib ko'rsating (masalan: 1 250 000 so'm / 1 250 000 руб.).
- Foiz: 1 kasr bilan ko'rsating (masalan: 23.5%).
- Katta raqamlar: mln/mlrd bilan ko'rsating (masalan: 12.5 mln so'm).

HECH QACHON:
- Bo'sh (NaN) qiymatlarni 0 deb hisoblamang.
- Matnli ustunni yig'indi (sum) qilmang.
- Taxminiy natija bermang.

3.2 USTUN NOMLARINI ANIQLASH
───────────────────────────
Foydalanuvchi "tushum", "daromad", "savdo" deb yozganda:
→ Fayldagi ENG YAQIN ustun nomini toping.
→ Agar 2 ta mos kelsa, foydalanuvchidan qaysi birini nazarda tutganligini so'rang.

3.3 SANA VA VAQT TAHLILI
────────────────────────
- Sana ustunini avtomatik aniqlang (DD.MM.YYYY, YYYY-MM-DD, va h.k.)
- Guruhlangan oylar va kunlar nomlarini tanlangan tilda yozing.
- Yil/Oy/Kun bo'yicha guruhlashda mantiqiy xronologik tartibni saqlang.

3.4 GRAFIK CHIZISH VA PROFESSIONAL VIZUALIZATSIYA QAIDALARI
───────────────────────────────────────────────────────────
Grafik chizish so'ralganda quyidagi professional vizualizatsiya qoidalariga qat'iy rioya qiling:
1. **Toifalar soni cheklovi**: Grafik chizishda (bar yoki doira diagrammasida) toifalar soni maksimal 8 ta bo'lsin. Agar toifalar ko'p bo'lsa, eng yirik 7 tasini qoldirib, qolganlarini "Boshqalar" (Others) guruhiga jamlang.
2. **Doira (Pie) diagrammasi cheklovlari**:
   - Agarda toifalar soni 5 tadan ortiq bo'lsa, HECH QACHON doira diagrammasi chizmang! Chunki yozuvlar ustma-ust tushib ketadi (pie chart overlap). Buning o'rniga doim gorizontal bar diagramma (`plt.barh`) chizing.
   - Gorizontal bar diagramma chizganda toifalar nomlari Y o'qida chiroyli ko'rinadi va ustma-ust tushmaydi.
   - Doira diagrammasida yozuvlar ustma-ust tushishini oldini olish uchun foizlarni (autopct) grafik ustida emas, balki legendda ko'rsating. Masalan: `plt.legend(bbox_to_anchor=(1, 1), loc='upper left')` va `labels=None` qilib, autopct ni o'chiring.
3. **Massiv Outlierlar va Shkalalar (Scale Flattening)**:
   - Agarda ma'lumotlarda keskin farq qiluvchi bitta ulkan toifa bo'lsa (masalan, outlier qiymat 1e16, boshqa qiymatlar esa 100), oddiy chiziqli shkalali bar/line grafik chizish barcha boshqa guruhlarni mutlaqo yassi (flat) va ko'rinmas qilib qo'yadi.
   - Bunday holda, doim logarifmik shkalani yoqing: `plt.yscale('log')` (yoki gorizontal bar bo'lsa `plt.xscale('log')`). Logarifmik shkala barcha kichik ustunlarni ham chiroyli ko'rsatadi.
4. **Grafik bezagi**:
   - Sarlavha va o'q nomlarini {language} tilida yozing.
   - Agarda X o'qidagi yozuvlar 5 tadan ko'p bo'lsa, ularni 45 darajaga burib yozing: `plt.xticks(rotation=45, ha='right')`.
   - Har safar grafik chizilgach, rasmni saqlashdan oldin `plt.tight_layout()` chaqiring.
5. Grafikni 'plot_1.png', 'plot_2.png' nomlari bilan saqlang va har doim `plt.close()` bilan yoping.

3.5 ILG'OR BIZNES TAHLILLAR (RFM, ABC TAHLIL, PROGNOZ)
──────────────────────────────────────────────────────
Foydalanuvchi quyidagi tahlillarni so'raganda mos mantiqiy Python kodini yozing:
A. RFM (Recency, Frequency, Monetary) Tahlili:
   - Mijozlarning oxirgi xarid kuni (Recency), xaridlar soni (Frequency) va jami xarid summasini (Monetary) hisoblang.
   - Har bir ko'rsatkich bo'yicha 1 dan 5 gacha ball bering (qanchalik yaqin/ko'p bo'lsa 5, aks holda 1).
   - Mijozlarni segmentlarga ajrating (VIP, Sodiq, Ketish arafasida, Yo'qotilgan) va ularga sodda tavsif bering.
B. ABC Tahlili (Daromad/Sotuv ulushi bo'yicha):
   - Mahsulotlarni/tizimlarni jami daromadga qo'shgan hissasi bo'yicha saralang.
   - Kumulyativ (yig'ma) ulushni hisoblang.
   - Guruhlang: 80% gacha bo'lganlar -> A klass (eng muhim), 80-95% -> B klass (o'rtacha), 95-100% -> C klass (kam sotiladigan).
C. Trendlar va Prognozlash (Forecasting):
   - Savdolar dinamikasini oylar/kunlar bo'yicha guruhlang.
   - Pandas rolling mean (sirpanuvchi o'rtacha) yoki oddiy chiziqli regressiya (Linear Regression - numpy polyfit yordamida) orqali keyingi davrlar uchun trend yo'nalishini hisoblab chiqib bering.

Jadval metadata strukturasi:
{metadata}

Foydalanuvchi so'rovi: "{user_query}"

================================================================
Vazifangiz:
================================================================
Foydalanuvchining so'roviga javob beruvchi faqatgina toza, xavfsiz va to'g'ri Python kodini yozing.
1. Faqat ```python ``` bloki ichida kod yozing. Blokdan tashqarida hech qanday izoh yoki matn bo'lmasligi kerak.
2. Jadval `df` nomi ostida global o'zgaruvchida allaqachon yuklangan. Uni fayldan o'qimang.
3. Foydalanuvchiga ma'lumotlarni juda tushunarli va vizual ko'rsatish uchun, tahlil davomida chiroyli grafiklar chizing.
   Grafiklarni mos ravishda 'plot_1.png', 'plot_2.png' va 'plot_3.png' nomlari bilan saqlang.
   Buning uchun har bir grafik chizilgach:
   `plt.savefig('plot_1.png', bbox_inches='tight')`
   `plt.close()`
4. Har doim `print()` yordamida tahliliy hisobotni chop eting. Chop etiladigan hisobot matni TO'LIQ {language} tilida bo'lishi shart!

Javobni chop etishda quyidagi shablonlardan mantiqan mos keladiganini ishlating:

SHABLON A (ODDIY HISOB-KITOB JAVOBI):
📊 [Savol matni]
Natija: [aniq raqam va birlik]
📌 Qo'shimcha ma'lumot:
• [tegishli statistika 1]
• [tegishli statistika 2]
💡 Tavsiya: [1 ta amaliy tavsiya — agar ma'lumot yetarli bo'lsa]

SHABLON B (TOP N TAHLILI):
🏆 Top [N]:
| # | [Nom ustuni] | [Qiymat ustuni] | Ulush |
|---|-------------|----------------|-------|
...
Jami: [umumiy yig'indi]
💡 [1 ta amaliy xulosa]

SHABLON C (OY/DAVR TAHLILI):
📅 [Davr] bo'yicha tahlil:
[Jadval yoki ro'yxat]
📈 O'sish: [eng yuqori o'sish davri] → +XX%
📉 Tushish: [eng past davr] → -XX%
💡 [Xulosa va tavsiya]

SHABLON D (UMUMIY HISOBOT):
📊 UMUMIY HISOBOT
🔢 ASOSIY KO'RSATKICHLAR:
• Jami: [qiymat]
• O'rtacha: [qiymat]
• Eng yuqori: [qiymat] ([qayerda/qachon])
• Eng past: [qiymat] ([qayerda/qachon])
🏆 LIDERLAR: [Top 3-5 ro'yxat]
📈 TENDENSIYA: [O'sish/tushish tahlili]
⚠️ MUAMMOLI JOYLAR: [Kamchiliklar — agar bo'lsa]
💡 3 TA ASOSIY TAVSIYA:
1. [Tavsiya 1]
2. [Tavsiya 2]
3. [Tavsiya 3]

Barcha matnlar, tavsiyalar, sarlavhalar va yo'riqnomalar to'liq {language} tilida chop etilishi kerak.
5. Kodda `os`, `sys`, `subprocess` yoki boshqa tizimli kutubxonalardan foydalanmang.
6. Python sintaksisiga qat'iy rioya qiling:
    - Qavslar (parentheses/brackets) to'liq ochilib-yopilganligini ta'minlang.
    - Hech qachon `if-else` yoki `try-except` bloklarini bo'sh (yoki faqat kommentlar bilan) qoldirmang, xatolik chiqmasligi uchun kamida `pass` yozing.
    - Har doim `df` jadvalidagi mavjud ustun nomlariga murojaat qiling.
"""

        current_prompt = prompt
        exec_attempts = 3
        
        for exec_attempt in range(exec_attempts):
            try:
                max_retries = 5
                backoff = 2
                response_text = ""
                success = False
                last_error = ""

                for attempt in range(max_retries):
                    try:
                        response = self.client.models.generate_content(
                            model='gemini-2.5-flash',
                            contents=current_prompt
                        )
                        response_text = response.text.strip()
                        success = True
                        break
                    except Exception as e:
                        last_error = str(e)
                        if attempt < max_retries - 1 and ("503" in last_error or "429" in last_error or "UNAVAILABLE" in last_error or "RESOURCE_EXHAUSTED" in last_error or "rate limit" in last_error.lower()):
                            time.sleep(backoff)
                            backoff += 3  # Delays: 2s, 5s, 8s, 11s (total 26s buffer)
                            continue
                        else:
                            break

                if not success:
                    return f"Gemini API bilan bog'lanishda xatolik yuz berdi: {last_error}", []
                
                # Extract python code block
                code_match = re.search(r'```python\s*(.*?)\s*```', response_text, re.DOTALL)
                if not code_match:
                    # Try raw text if no markdown block
                    code = response_text
                else:
                    code = code_match.group(1)
                    
                if not code.strip():
                    if exec_attempt < exec_attempts - 1:
                        current_prompt = current_prompt + "\n\nAI tahlil kodini generatsiya qila olmadi. Iltimos, faqat ```python ``` bloki ichida to'liq Python kodini qaytadan yozing."
                        continue
                    return "AI tahlil kodini generatsiya qila olmadi. Iltimos, so'rovni boshqacha yozib ko'ring.", []
                    
                # Safety Check
                if not self._is_code_safe(code):
                    return "Xavfsizlik cheklovi: AI tomonidan generatsiya qilingan kodda zararli buyruqlar aniqlandi va bajarilmadi.", []
                
                # Execute code locally
                # Redirect stdout to capture prints
                old_stdout = sys.stdout
                redirected_output = io.StringIO()
                sys.stdout = redirected_output
                
                # Setup execution local variables
                # Create a clean copy of the dataframe
                exec_globals = {
                    'df': df.copy(),
                    'pd': pd,
                    'np': np,
                    'plt': plt,
                    'sns': sns
                }
                
                error_occurred = None
                try:
                    # Execute the generated code
                    exec(code, exec_globals)
                except Exception as e:
                    error_occurred = str(e)
                finally:
                    sys.stdout = old_stdout
                    
                if error_occurred:
                    logger.warning(f"Self-healing attempt {exec_attempt + 1} failed: {error_occurred}")
                    if exec_attempt < exec_attempts - 1:
                        # Construct healing prompt
                        current_prompt = prompt + f"\n\nSiz yozgan oldingi Python kodini bajarishda quyidagi xatolik yuz berdi:\n`{error_occurred}`\n\nOldingi kod:\n```python\n{code}\n```\n\nIltimos, ushbu xatolikni bartaraf qilib, faqat tuzatilgan to'liq Python kodini ```python ``` bloki ichida yozib bering. Hech qanday boshqa izoh yozmang, faqat kod bo'lsin."
                        continue
                    return f"Tahlil kodini bajarishda xatolik yuz berdi:\n`{error_occurred}`\n\nAI yozgan kod:\n```python\n{code}\n```", []
                    
                output_text = redirected_output.getvalue().strip()
                
                # Gather all generated plots
                plots = []
                for i in range(1, 4):
                    p_name = f"plot_{i}.png"
                    if os.path.exists(p_name):
                        unique_name = f"plot_{uuid.uuid4().hex[:8]}_{i}.png"
                        unique_path = os.path.abspath(unique_name)
                        try:
                            os.rename(p_name, unique_path)
                            plots.append(unique_path)
                        except:
                            plots.append(os.path.abspath(p_name))
                
                # Legacy single plot compatibility
                if os.path.exists(plot_path):
                    plots.append(plot_path)
                    
                if not output_text:
                    if plots:
                        output_text = "Grafiklar muvaffaqiyatli tayyorlandi."
                    else:
                        output_text = "Tahlil yakunlandi, ammo hech qanday natija chop etilmadi."
                        
                return output_text, plots
                
            except Exception as e:
                logger.error(f"Error during self-healing iteration: {e}")
                if exec_attempt < exec_attempts - 1:
                    current_prompt = prompt + f"\n\nTahlil jarayonida kutilmagan ichki xatolik yuz berdi: {str(e)}. Iltimos, qaytadan boshqa usulda kod yozib ko'ring."
                    continue
                return f"Gemini API bilan bog'lanishda xatolik yuz berdi: {str(e)}", []


    def get_file_welcome_report(self, df: pd.DataFrame, language: str = 'uz') -> str:
        """
        Generates the QISM 2 welcome and QISM 6 data quality checking report in the chosen language.
        """
        cols = list(df.columns)
        total_rows = df.shape[0]
        
        # Determine data types description and identify ID columns
        all_num_cols = list(df.select_dtypes(include=[np.number]).columns)
        id_cols = [c for c in all_num_cols if self.is_likely_id_column(c, df[c])]
        num_cols = [c for c in all_num_cols if c not in id_cols]
        
        # Simple data type classification
        if len(all_num_cols) == len(cols):
            dt_type = {
                'uz': "faqat raqamli",
                'ru': "только числовые",
                'en': "numeric only"
            }.get(language, "faqat raqamli")
        elif len(all_num_cols) == 0:
            dt_type = {
                'uz': "faqat matnli",
                'ru': "только текстовые",
                'en': "textual only"
            }.get(language, "faqat matnli")
        else:
            dt_type = {
                'uz': "aralash (raqamlar, matn, sana)",
                'ru': "смешанный (числа, текст, даты)",
                'en': "mixed (numbers, text, dates)"
            }.get(language, "aralash")
            
        # Data quality checks
        warnings = []
        
        # 0. ID columns warning
        for c in id_cols:
            if language == 'uz':
                warnings.append(f"• `{c}` ustuni raqamli bo'lsa-da, u identifikator (ID, kod yoki hisob raqami) sifatida aniqlandi. U bo'yicha yig'ildi (summa) hisoblash mantiqan xato hisoblanadi (faqat sonini sanash tavsiya etiladi).")
            elif language == 'ru':
                warnings.append(f"• Столбец `{c}` является числовым, но распознан как идентификатор (ID, код или счет). Суммирование по нему не имеет логического смысла (рекомендуется только подсчет количества).")
            else:
                warnings.append(f"• Column `{c}` is numeric but recognized as an identifier (ID, code, or account). Summing it does not make logical sense (only counting is recommended).")

        # 1. Null values
        null_counts = df.isnull().sum()
        null_cols = null_counts[null_counts > 0]
        for c, val in null_cols.items():
            if language == 'uz':
                warnings.append(f"• `{c}` ustunida {val} ta bo'sh katak bor. Hisoblashda e'tiborga olinmaydi.")
            elif language == 'ru':
                warnings.append(f"• В столбце `{c}` обнаружено {val} пустых ячеек. Они не будут учитываться при расчетах.")
            else:
                warnings.append(f"• Column `{c}` has {val} empty cells. They will be ignored in calculations.")
                
        # 2. Duplicates
        dup_count = df.duplicated().sum()
        if dup_count > 0:
            if language == 'uz':
                warnings.append(f"• {dup_count} ta takroriy (dublikat) qator topildi. Tozalash tugmasi orqali o'chirishingiz mumkin.")
            elif language == 'ru':
                warnings.append(f"• Обнаружено {dup_count} повторяющихся строк. Вы можете удалить их с помощью кнопки очистки.")
            else:
                warnings.append(f"• Found {dup_count} duplicate rows. You can clean them using the Clean button.")
                
        # 3. Outliers (only on actual numeric columns, not IDs)
        for c in num_cols:
            col_clean = df[c].dropna()
            if len(col_clean) > 5:
                q25 = col_clean.quantile(0.25)
                q75 = col_clean.quantile(0.75)
                iqr = q75 - q25
                outliers = col_clean[(col_clean < q25 - 3 * iqr) | (col_clean > q75 + 3 * iqr)]
                if len(outliers) > 0:
                    out_val = outliers.iloc[0]
                    # format with thousands separator
                    try:
                        f_val = f"{out_val:,.1f}"
                    except:
                        f_val = str(out_val)
                    if language == 'uz':
                        warnings.append(f"• `{c}` ustunida keskin farq qiluvchi qiymat: {f_val}. Bu to'g'rimi?")
                    elif language == 'ru':
                        warnings.append(f"• Необычное значение в столбце `{c}`: {f_val}. Пожалуйста, проверьте.")
                    else:
                        warnings.append(f"• Unusual value in column `{c}`: {f_val}. Please verify.")
                    break # Only warn about one to keep it clean
                    
        # Select suggestion columns
        sum_col = num_cols[0] if num_cols else (id_cols[0] if id_cols else cols[0])
        group_col = df.select_dtypes(include=['object']).columns[0] if len(df.select_dtypes(include=['object']).columns) > 0 else cols[0]
        is_sum_col_id = sum_col in id_cols

        # Build text based on selected language
        if language == 'uz':
            warn_text = "\n".join(warnings) if warnings else "• Hech qanday kamchiliklar topilmadi. Ma'lumotlar sifati juda yaxshi! ✅"
            cols_joined = ", ".join(cols[:15]) + ("..." if len(cols) > 15 else "")
            
            sum_query_suggest = f"\"Takrorlanmas `{sum_col}`lar sonini hisobla\"" if is_sum_col_id else f"\"Jami `{sum_col}`ni hisobla\""
            
            msg = (
                "✅ Fayl muvaffaqiyatli qabul qilindi!\n\n"
                "📋 **Faylingiz haqida:**\n"
                f"• Ustunlar: {cols_joined}\n"
                f"• Jami qatorlar: {total_rows} ta\n"
                f"• Ma'lumot turi: {dt_type}\n\n"
                f"⚠️ **Data Quality (Ma'lumotlar sifati) tekshiruvi:**\n{warn_text}\n\n"
                "Endi savolingizni bering. Masalan:\n"
                f"▶ {sum_query_suggest}\n"
                f"▶ \"Eng ko'p `{group_col}` bo'yicha top 5\"\n"
                f"▶ \"`{sum_col}`ning `{group_col}` bo'yicha grafigini chiz\""
            )
        elif language == 'ru':
            warn_text = "\n".join(warnings) if warnings else "• Проблем не обнаружено. Качество данных отличное! ✅"
            cols_joined = ", ".join(cols[:15]) + ("..." if len(cols) > 15 else "")
            
            sum_query_suggest = f"\"Посчитай количество уникальных `{sum_col}`\"" if is_sum_col_id else f"\"Посчитай общую сумму по `{sum_col}`\""
            
            msg = (
                "✅ Файл успешно принят!\n\n"
                "📋 **О вашем файле:**\n"
                f"• Столбцы: {cols_joined}\n"
                f"• Всего строк: {total_rows}\n"
                f"• Тип данных: {dt_type}\n\n"
                f"⚠️ **Проверка качества данных (Data Quality):**\n{warn_text}\n\n"
                "Теперь задайте ваш вопрос. Например:\n"
                f"▶ {sum_query_suggest}\n"
                f"▶ \"Топ-5 по столбцу `{group_col}`\"\n"
                f"▶ \"Построй график `{sum_col}` по `{group_col}`\""
            )
        else: # English
            warn_text = "\n".join(warnings) if warnings else "• No issues found. Data quality is excellent! ✅"
            cols_joined = ", ".join(cols[:15]) + ("..." if len(cols) > 15 else "")
            
            sum_query_suggest = f"\"Count unique `{sum_col}`\"" if is_sum_col_id else f"\"Calculate total for `{sum_col}`\""
            
            msg = (
                "✅ File accepted successfully!\n\n"
                "📋 **About your file:**\n"
                f"• Columns: {cols_joined}\n"
                f"• Total rows: {total_rows}\n"
                f"• Data type: {dt_type}\n\n"
                f"⚠️ **Data Quality check:**\n{warn_text}\n\n"
                "Now ask your question. For example:\n"
                f"▶ {sum_query_suggest}\n"
                f"▶ \"Top 5 by `{group_col}`\"\n"
                f"▶ \"Draw a chart of `{sum_col}` by `{group_col}`\""
            )
        return msg
