Modbus Air Conditioner GUI Simulation
A Python project developed using PyModbus to simulate an air conditioner device and Modbus TCP communication via a graphical user interface (GUI).
📌 About the Project
This project creates a software-based Modbus TCP air conditioner simulator instead of a physical AC device. Air conditioner states can be monitored and controlled through the GUI.
Key Objectives
Test Modbus TCP communication
Simulate air conditioner control scenarios
Visually monitor register and coil values
Perform Modbus client/server tests without physical hardware
Develop device simulation using PyModbus
✨ Features
Modbus TCP Server simulation
Displaying AC status via GUI
Temperature and target temperature control
Power ON/OFF control
Operating mode and fan speed control
Monitoring of Modbus register values
Connection status indicator
Log screen
Auto-connection check and reconnection
🛠️ Technologies Used
Python 3
PyModbus
Tkinter / CustomTkinter
Modbus TCP
Threading / Asynchronous operations
📦 Installation
Ensure you have Python 3 installed.
pip install pymodbus
If a requirements.txt file is present in the project:
pip install -r requirements.txt
▶️ Running the Project
In the project directory:
python main.py
(Run the relevant .py file if the main entry point has a different name).
🌐 Modbus TCP Configuration
Sample connection settings:
Setting
Value
Protocol
Modbus TCP
IP
127.0.0.1
Port
5020
Slave / Device ID
1

(The port number can be changed based on the actual configuration in the code).
🧩 Sample Register Structure
Register
Description
Example Value & Meaning
0
AC Status
0 = Off, 1 = On
1
Set Temperature
24
2
Ambient Temperature
22
3
Operating Mode
0 = Auto, 1 = Cool, 2 = Heat
4
Fan Speed
0 = Auto, 1 = Low, 2 = Medium, 3 = High

(This table is an example; actual addressing should be evaluated based on the definitions inside the source code).
🔌 Automatic Connection
When the connection drops, the application is expected to:
Detect the disconnection
Write a warning to the log screen
Attempt to reconnect
Update the status once the connection is successfully re-established
If Connections Keep Dropping, Check:
Is the Modbus server actually running?
Is the IP address correct?
Is the port correct?
Is another program using the same port?
Do the client and server settings match?
Is the server thread/process shutting down?
Is the socket timeout set too low?
Is there an accidental close() call closing the connection inside the code logic?
⚠️ PyModbus Version Warnings
In newer versions of PyModbus, some legacy APIs have been deprecated. For example, warnings regarding classes such as ModbusDeviceContext, ModbusSequentialDataBlock, ModbusSparseDataBlock.
In PyModbus 4.x development versions, migrating to the new SimData / SimDevice structure is recommended. These warnings do not always crash the program; they indicate the use of older APIs.
👉 Official Documentation: https://pymodbus.readthedocs.io/
📝 Log System
Sample log output:
16:07:51 | INFO    | Modbus Server started
16:07:52 | INFO    | Client connection accepted
16:07:53 | INFO    | Register 1 -> 24
16:07:55 | WARNING | Client connection lost
16:07:56 | INFO    | Reconnecting...
🗂️ Recommended Project Structure
modbus-klima/
├── main.py
├── server.py
├── client.py
├── klima.py
├── gui.py
├── requirements.txt
├── README.md
└── logs/
    └── application.log
🧪 Test
Start the Modbus server.
Run the GUI application.
Check the client connection.
Test power ON/OFF commands.
Change the temperature value.
Modify mode and fan values.
Stop the server to test the disconnection handling behavior.
Restart the server to test automatic reconnection.
🐛 Troubleshooting
Connection refused: The server may not be running or an incorrect port is being used (IP: 127.0.0.1, Port: 5020).
Frequent disconnections: Check the server lifecycle and client connection management. Specifically, ensure that client.close() is not mistakenly placed inside a connection loop.
Deprecation warnings: Ensure compatibility with your installed PyModbus version. Consider migrating to the new SimData/SimDevice API for long-term support.
🔄 Development Roadmap
Real-time temperature graph
Advanced Modbus register management
Holding Register / Input Register support
Coil control
Multiple air conditioner simulation
Manual / automatic operation modes
Error codes and alarm system
CSV/JSON logging
Advanced reconnection algorithm
Migration to PyModbus 4.x SimData/SimDevice API
📄 License
This project is prepared for educational, development, and testing purposes.
Modbus Klima GUI Simülasyonu
PyModbus kullanılarak geliştirilmiş, klima cihazını ve Modbus TCP haberleşmesini görsel bir arayüz üzerinden simüle etmeyi amaçlayan Python projesidir.
📌 Proje Hakkında
Bu proje, gerçek bir klima cihazı yerine yazılım tabanlı bir Modbus TCP klima simülatörü oluşturur. GUI üzerinden klima durumları izlenebilir ve kontrol edilebilir.
Temel Amaçlar
Modbus TCP haberleşmesini test etmek
Klima kontrol senaryolarını simüle etmek
Register ve coil değerlerini görsel olarak izlemek
Gerçek cihaz olmadan Modbus istemci/sunucu testleri yapmak
PyModbus ile cihaz simülasyonu geliştirmek
✨ Özellikler
Modbus TCP Server simülasyonu
Klima durumlarının GUI üzerinden gösterilmesi
Sıcaklık ve hedef sıcaklık kontrolü
Klima açma/kapama
Çalışma modu ve fan hızı kontrolü
Modbus register değerlerinin izlenmesi
Bağlantı durumunun gösterilmesi
Log ekranı
Otomatik bağlantı kontrolü ve yeniden bağlanma
🛠️ Kullanılan Teknolojiler
Python 3
PyModbus
Tkinter / CustomTkinter
Modbus TCP
Threading / Asenkron işlemler
📦 Kurulum
Python 3 kurulu olduğundan emin olun.
pip install pymodbus
Projede requirements.txt varsa:
pip install -r requirements.txt
▶️ Çalıştırma
Proje klasöründe:
python main.py
(Ana dosyanın adı farklıysa ilgili .py dosyasını çalıştırın).
🌐 Modbus TCP Bağlantı Ayarları
Örnek bağlantı ayarları:
Ayar
Değer
Protokol
Modbus TCP
IP
127.0.0.1
Port
5020
Slave / Device ID
1

(Port numarası projedeki gerçek yapılandırmaya göre değiştirilebilir).
🧩 Örnek Register Yapısı
Register
Açıklama
Örnek Değer ve Anlamı
0
Klima durumu
0 = Kapalı, 1 = Açık
1
Set sıcaklığı
24
2
Ortam sıcaklığı
22
3
Çalışma modu
0 = Auto, 1 = Cool, 2 = Heat
4
Fan hızı
0 = Auto, 1 = Low, 2 = Medium, 3 = High

(Bu tablo örnektir; gerçek adresleme kod içerisindeki tanımlara göre değerlendirilmelidir).
🔌 Otomatik Bağlantı
Bağlantı koptuğunda uygulamanın:
Kopmayı algılaması
Log ekranına uyarı yazması
Yeniden bağlanmayı denemesi
Bağlantı başarılı olduğunda durumu güncellemesi
beklenir.
Bağlantı Sürekli Kesiliyorsa Şunları Kontrol Edin:
Modbus server gerçekten çalışıyor mu?
IP adresi doğru mu?
Port doğru mu?
Portu başka bir program kullanıyor mu?
Client ve server ayarları aynı mı?
Server thread/process'i kapanıyor mu?
Socket timeout çok düşük mü?
Kod içerisinde bağlantıyı kapatan gereksiz bir close() çağrısı var mı?
⚠️ PyModbus Sürüm Uyarıları
PyModbus'un yeni sürümlerinde bazı eski API'ler deprecated durumuna getirilmiştir. Örneğin: ModbusDeviceContext, ModbusSequentialDataBlock, ModbusSparseDataBlock sınıfları.
PyModbus 4.x geliştirme sürümlerinde yeni SimData / SimDevice yapısına geçiş önerilmektedir. Bu uyarılar her zaman programın çalışmasını engelleyen hatalar değildir; kullanılan API'nin eski olduğunu gösterebilir.
👉 Güncel Dokümantasyon: https://pymodbus.readthedocs.io/
📝 Log Sistemi
Örnek log çıktısı:
16:07:51 | INFO    | Modbus Server başlatıldı
16:07:52 | INFO    | Client bağlantısı kabul edildi
16:07:53 | INFO    | Register 1 -> 24
16:07:55 | WARNING | Client bağlantısı kesildi
16:07:56 | INFO    | Yeniden bağlanılıyor...
🗂️ Önerilen Proje Yapısı
modbus-klima/
├── main.py
├── server.py
├── client.py
├── klima.py
├── gui.py
├── requirements.txt
├── README.md
└── logs/
    └── application.log
🧪 Test
Modbus server'ı başlatın.
GUI uygulamasını çalıştırın.
Client bağlantısını kontrol edin.
Klima açma/kapama komutlarını test edin.
Sıcaklık değerini değiştirin.
Mod ve fan değerlerini değiştirin.
Server'ı kapatıp bağlantı kopma davranışını test edin.
Server'ı tekrar başlatıp otomatik yeniden bağlantıyı test edin.
🐛 Sorun Giderme
Connection refused: Server çalışmıyor veya yanlış port kullanılıyor olabilir (IP: 127.0.0.1, Port: 5020).
Bağlantı sürekli kopuyor: Server yaşam döngüsünü ve client bağlantı yönetimini kontrol edin. Özellikle client.close() çağrısının yanlışlıkla bağlantı döngüsünün içinde bulunmadığından emin olun.
Deprecated uyarıları: Kullanılan PyModbus sürümü ile API'nin uyumlu olduğundan emin olun. Uzun vadede yeni SimData/SimDevice API'sine geçiş yapılmalıdır.
🔄 Geliştirme Planı
Gerçek zamanlı sıcaklık grafiği
Gelişmiş Modbus register yönetimi
Holding Register / Input Register desteği
Coil kontrolü
Birden fazla klima simülasyonu
Manuel / otomatik çalışma modu
Hata kodları ve alarm sistemi
CSV/JSON loglama
Gelişmiş yeniden bağlantı algoritması
PyModbus 4.x SimData/SimDevice API'sine geçiş
📄 Lisans
Bu proje eğitim, geliştirme ve test amacıyla hazırlanmıştır.
