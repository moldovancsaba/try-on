from flask import Flask, render_template, request, jsonify, send_from_directory
import os
import json
from werkzeug.utils import secure_filename
import shutil

app = Flask(__name__)

STUDIO_DIR = '/Users/Shared/Projects/try-on/studio_tools'
PACKAGES_DIR = os.path.join(STUDIO_DIR, 'packages')
MAPS_DIR = os.path.join(STUDIO_DIR, 'master_maps')
UPLOADS_DIR = os.path.join(STUDIO_DIR, 'uploads')

os.makedirs(PACKAGES_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/maps/<path:filename>')
def serve_maps(filename):
    return send_from_directory(MAPS_DIR, filename)

@app.route('/uploads/<path:filename>')
def serve_uploads(filename):
    return send_from_directory(UPLOADS_DIR, filename)

@app.route('/upload_garment', methods=['POST'])
def upload_garment():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
        
    filename = file.filename
    save_path = os.path.join(UPLOADS_DIR, filename)
    file.save(save_path)
    return jsonify({'url': f'/uploads/{filename}', 'filename': filename})

@app.route('/save_package', methods=['POST'])
def save_package():
    data = request.json
    package_name = data.get('package_name', 'default_package')
    
    package_dir = os.path.join(PACKAGES_DIR, package_name)
    os.makedirs(package_dir, exist_ok=True)
    
    # Save JSON
    json_path = os.path.join(package_dir, 'package.json')
    with open(json_path, 'w') as f:
        json.dump(data, f, indent=4)
        
    # Copy garment image to package
    garment_filename = data.get('garment_filename')
    if garment_filename:
        src_img = os.path.join(UPLOADS_DIR, garment_filename)
        if os.path.exists(src_img):
            shutil.copy(src_img, os.path.join(package_dir, garment_filename))
            
    return jsonify({'success': True, 'path': package_dir})

if __name__ == '__main__':
    app.run(debug=True, port=5001)
