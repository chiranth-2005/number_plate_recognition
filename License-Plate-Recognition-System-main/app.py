import cv2
import pytesseract
from flask import Flask, render_template, Response, request, redirect, url_for, session
import os
import sqlite3
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'your_secret_key'
UPLOAD_FOLDER = 'static/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Ensure tesseract path is set if needed
# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

def init_db():
    conn = sqlite3.connect('data.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS uploads (filename TEXT, text TEXT, timestamp TEXT)''')
    c.execute("INSERT OR IGNORE INTO users (username, password) VALUES ('admin', 'admin')")
    conn.commit()
    conn.close()

init_db()

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = sqlite3.connect('data.db')
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE username = ? AND password = ?", (username, password))
        user = c.fetchone()
        conn.close()
        if user:
            session['username'] = username
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error="Invalid credentials")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/index')
def index():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    if 'username' not in session:
        return redirect(url_for('login'))

    file = request.files['image']
    if file:
        filename = file.filename
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        # Read and preprocess the image
        image = cv2.imread(filepath)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.bilateralFilter(gray, 11, 17, 17)
        edged = cv2.Canny(gray, 30, 200)

        # Find contours to detect possible plate area
        contours, _ = cv2.findContours(edged.copy(), cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        contours = sorted(contours, key=cv2.contourArea, reverse=True)[:10]
        plate = None

        for c in contours:
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.018 * peri, True)
            if len(approx) == 4:
                x, y, w, h = cv2.boundingRect(approx)
                plate = gray[y:y + h, x:x + w]
                break

        if plate is None:
            # fallback if no contour matched
            plate = gray

        # Resize and threshold for better OCR
        plate = cv2.resize(plate, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        _, thresh = cv2.threshold(plate, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # Use OCR with specific config
        config = '--oem 3 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
        text = pytesseract.image_to_string(thresh, config=config)

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect('data.db')
        c = conn.cursor()
        c.execute("INSERT INTO uploads (filename, text, timestamp) VALUES (?, ?, ?)",
                  (filename, text.strip(), timestamp))
        conn.commit()
        conn.close()

        return render_template('result.html', image=filename, text=text.strip())

    return "Upload failed"


@app.route('/history')
def history():
    if 'username' not in session:
        return redirect(url_for('login'))
    conn = sqlite3.connect('data.db')
    c = conn.cursor()
    c.execute("SELECT * FROM uploads ORDER BY timestamp DESC")
    data = c.fetchall()
    conn.close()
    return render_template('history.html', data=data)

def gen_frames():
    camera = cv2.VideoCapture(0)
    while True:
        success, frame = camera.read()
        if not success:
            break

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.bilateralFilter(gray, 11, 17, 17)
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
        data = pytesseract.image_to_data(thresh, output_type=pytesseract.Output.DICT)

        found_text = ""
        for i in range(len(data['text'])):
            if int(data['conf'][i]) > 60 and data['text'][i].strip() != "":
                x, y, w, h = data['left'][i], data['top'][i], data['width'][i], data['height'][i]
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                cv2.putText(frame, data['text'][i], (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                found_text += data['text'][i] + " "

        if found_text.strip():
            filename = f"auto_{datetime.now().strftime('%Y%m%d%H%M%S')}.jpg"
            path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            cv2.imwrite(path, frame)
            conn = sqlite3.connect('data.db')
            c = conn.cursor()
            c.execute("INSERT INTO uploads (filename, text, timestamp) VALUES (?, ?, ?)",
                      (filename, found_text.strip(), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
            conn.close()

        _, buffer = cv2.imencode('.jpg', frame)
        frame = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

@app.route('/camera')
def camera():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('camera.html')

@app.route('/video_feed')
def video_feed():
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/change_credentials', methods=['GET', 'POST'])
def change_credentials():
    if 'username' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        new_user = request.form['new_username']
        new_pass = request.form['new_password']
        conn = sqlite3.connect('data.db')
        c = conn.cursor()
        c.execute("UPDATE users SET username = ?, password = ? WHERE username = ?", (new_user, new_pass, session['username']))
        conn.commit()
        session['username'] = new_user
        conn.close()
        return redirect(url_for('index'))
    return render_template('change_credentials.html')

if __name__ == '__main__':
    app.run(debug=True)
