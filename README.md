# Reminder Bot

Telegram üzerinden Türkçe metin ve sesli mesajlarla etkinlik oluşturmayı, listelemeyi ve silmeyi sağlayan kişisel takvim botu. Etkinlikler CalDAV sunucusunda saklanır; saatli etkinlikler için Telegram bildirimi planlanır.

## Özellikler

- Doğal dille tek gün veya birden fazla gün için etkinlik oluşturma.
- Saat belirtilmeyen etkinlikleri tüm gün olarak kaydetme.
- Etkinliklere açıklama ve not ekleme.
- Başlık üzerinden etkinlik silme ve yaklaşan etkinlikleri listeleme.
- Gemini ile sesli mesajları Türkçe metne çevirme.
- Metin ve sesli mesaj işlemlerini tek bir Telegram sohbetiyle sınırlama.

## Nasıl çalışır?

1. Bot, Telegram mesajlarını polling yöntemiyle alır.
2. Sesli mesaj varsa kayıt Gemini'ye yüklenerek metne çevrilir.
3. OpenRouter metni analiz eder ve yapılacak işlemi JSON olarak döndürür. İlk model başarısız olursa `openrouter/free` denenir.
4. Etkinlikler CalDAV üzerinde, adında `reminder` geçen ilk takvime kaydedilir. Böyle bir takvim yoksa `reminder` adında oluşturulur.
5. Saatli etkinlikler için `JobQueue` ile etkinlik başlangıcında Telegram bildirimi gönderilir.

Tarih ve saatler uygulamada `Europe/Istanbul` saat dilimine göre yorumlanır.

## Gereksinimler

- `zoneinfo` modülünü destekleyen Python kurulumu (Python 3.9 ve sonrası). Kurulan paketler daha yeni bir Python sürümü gerektirebilir.
- Telegram bot token'ı ve kullanılacak sohbetin sayısal kimliği.
- Erişilebilir bir CalDAV sunucusu (örneğin Radicale) ve takvim oluşturma/yazma izni olan bir hesap.
- OpenRouter ve Gemini API anahtarları. Mevcut kod her iki istemciyi de başlangıçta oluşturur.
- Telegram, CalDAV, OpenRouter ve Gemini servislerine ağ erişimi.

## Kurulum

Terminalde `main.py` ve `requirements.txt` dosyalarının bulunduğu dizine geçin.

### Windows / PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

`requirements.txt`, CalDAV, Telegram, ortam değişkenleri, iCalendar, OpenRouter ve Gemini işlemleri için gereken Python paketlerini listeler. `python-telegram-bot[job-queue]`, bildirim zamanlama desteğini de kurar. Paket sürümleri şu anda sabitlenmemiştir.

## Yapılandırma

Proje dizininde `.env` adlı bir dosya oluşturun ve örnek değerleri kendi bilgilerinizle değiştirin:

```dotenv
RADICALE_URL=https://calendar.example.com
RADICALE_USER=your_calendar_username
RADICALE_PASS=your_calendar_password
TELEGRAM_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=123456789
OPENROUTER_API_KEY=your_openrouter_api_key
GEMINI_API_KEY=your_gemini_api_key
```

| Değişken | Açıklama |
| --- | --- |
| `RADICALE_URL` | CalDAV sunucusunun adresi. Kodda kişisel bir varsayılan bulunduğundan kendi adresinizi açıkça tanımlayın. |
| `RADICALE_USER` | CalDAV kullanıcı adı. Kodda kişisel bir varsayılan bulunduğundan kendi hesabınızı tanımlayın. |
| `RADICALE_PASS` | CalDAV hesabının parolası. |
| `TELEGRAM_TOKEN` | Telegram'da BotFather üzerinden oluşturulan botun token'ı. |
| `TELEGRAM_CHAT_ID` | Botun işlemlerine izin verilen sohbetin sayısal kimliği; grup kimlikleri negatif olabilir. |
| `OPENROUTER_API_KEY` | Mesajların anlamlandırılması için kullanılan API anahtarı. |
| `GEMINI_API_KEY` | Sesli mesajların metne çevrilmesi için kullanılan API anahtarı. |

`TELEGRAM_CHAT_ID` başlangıçta tam sayıya çevrilir; boş veya sayısal olmayan bir değer uygulamanın başlamasını engeller. `.env`, mevcut `.gitignore` tarafından dışlanır; token ve parolaları kaynak koduna veya commit'lere eklemeyin.

## Çalıştırma ve kullanım

Sanal ortam etkin durumdayken:

```bash
python main.py
```

Terminalde `Bot started.` mesajı göründükten sonra yapılandırılmış Telegram sohbetinde bota `/start` gönderin. Ardından metin veya sesli mesaj kullanabilirsiniz:

| Amaç | Örnek mesaj |
| --- | --- |
| Saatli etkinlik | Yarın saat 15:00'a sinema hatırlatıcısı oluştur. |
| Tüm gün etkinlik | Yarın kitap fuarını takvime ekle. |
| Birden fazla gün | 24 ve 25'inde toplantı var, takvime ekle. |
| Açıklama ekleme | Yarın saat 14:00'a toplantı ekle, not: sunumu hazırla. |
| Listeleme | Yaklaşan etkinlikleri göster. |
| Bugünü listeleme | Bugünkü etkinlikleri göster. |
| Silme | Sinema etkinliğini sil. |

Botu durdurmak için terminalde `Ctrl+C` kullanın. Bildirimlerin gönderilebilmesi için bot çalışır durumda olmalıdır. Mesaj yorumlama AI çıktısına bağlı olduğundan oluşturma yanıtındaki tarih ve başlığı kontrol edin.

## Proje yapısı

```text
.
├── main.py           # Bot, AI istemcileri, takvim işlemleri ve bildirimler
├── requirements.txt  # Python bağımlılıkları
├── .gitignore        # Git'e dahil edilmeyecek dosyalar
└── README.md         # Kurulum ve kullanım bilgileri
```

`main.py` tek dosyalık bir uygulamadır. Dosyanın sonundaki `if __name__ == "__main__":` bloğu, doğrudan çalıştırıldığında `main()` fonksiyonunu çağırır.

## Mevcut sınırlamalar

- Tüm gün etkinlikler takvime kaydedilir ancak Telegram bildirimi planlanmaz.
- Saatli etkinlikler bir saat sürer. Birden fazla gün için saatli etkinlik oluşturulurken her günün saati kodda `09:00` olarak belirlenir.
- `PicklePersistence` kullanılsa da zamanlanmış `JobQueue` bildirimleri yeniden başlatmada geri yüklenmez. Takvim kayıtları CalDAV'da kalır; uygulama başlangıçta bunlardan bildirim oluşturmaz.
- Etkinlik silme, bugünden bir yıl önce ile bir yıl sonrası arasında başlıkta geçen metne göre eşleşen tüm kayıtları siler. Silme onayı istenmez ve önceden planlanmış Telegram bildirimi iptal edilmez.
- “Yarın” listelemesinin arama aralığı bugünün başlangıcından itibaren iki gündür; bugünkü kayıtlar da sonuçlarda görünebilir.
- Saatli iCalendar kayıtları `TZID` veya UTC işareti olmadan yazılır. Takvim istemcisindeki görüntüleme saati istemci/sunucu ayarlarına bağlı olabilir.
- Metin ve ses işleyicileri sohbet kimliğini kontrol eder; `/start` komutu bu kontrolü yapmaz.
- Model adları `main.py` içinde sabittir. Servislerdeki model erişimi değişirse kodun güncellenmesi gerekebilir.

## Veri işleme

Metin mesajları yorumlanmak üzere OpenRouter'a gönderilir. Sesli mesajlar önce yerel bir `.ogg` dosyasına indirilir, ardından Gemini'ye yüklenir; transkript OpenRouter'a gönderilir. Başarılı ses işleme sonrasında yerel dosya silinir ve Gemini dosyasını silme işlemi denenir. Hata durumlarında dosyalar kalabilir.

Etkinlik başlıkları ve açıklamaları CalDAV sunucusunda saklanır. Uygulama çalışırken `bot_persistence.pkl` dosyası oluşturulabilir; bu dosya ve geçici ses kayıtları mevcut `.gitignore` içinde ayrıca dışlanmamıştır. Commit öncesinde bu dosyaları kontrol edin.

## Sorun giderme

| Belirti | Kontrol |
| --- | --- |
| Başlangıçta `TELEGRAM_CHAT_ID` hatası | `.env` içindeki değerin tanımlı ve tam sayı olduğunu kontrol edin. |
| `ZoneInfoNotFoundError` | Ortamda saat dilimi verisi yoksa `python -m pip install tzdata` ile kurun. Özellikle Windows'ta gerekebilir. |
| Bot mesajlara yanıt vermiyor | Token'ı, `TELEGRAM_CHAT_ID` değerini, ağ bağlantısını ve çalışan bot sürecini kontrol edin. |
| Takvim işlemi başarısız | CalDAV adresini, hesap bilgilerini ve takvim yazma/oluşturma izinlerini kontrol edin. |
| AI sunucuları cevap vermedi | OpenRouter anahtarını, servis erişimini, kotayı ve kodda kullanılan modellerin erişilebilirliğini kontrol edin. |
| Sesli mesaj anlaşılamadı | Gemini anahtarını, model erişimini ve terminaldeki hata kaydını kontrol edin. |
| Yeniden başlatmadan sonra bildirim gelmedi | Zamanlanmış bildirimlerin yeniden yüklenmemesi mevcut bir sınırlamadır. |

## Geliştirme ve lisans

Depoda otomatik test paketi veya bağımlılık kilit dosyası bulunmuyor. Davranış değişikliklerini ayrı bir test botu ve takvimle; etkinlik oluşturma, listeleme, silme ve bildirim akışlarını kontrol ederek doğrulayın.

Depoda bir lisans dosyası bulunmuyor; kullanım ve dağıtım koşulları ayrıca belirlenmelidir.
