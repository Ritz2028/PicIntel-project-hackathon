// ImageTrace Pro Upload Handler
class ImageUploader {
    constructor() {
        this.dropZone = document.getElementById('drop-zone');
        this.fileInput = document.getElementById('file-input');
        this.browseBtn = document.getElementById('browse-btn');
        this.uploadForm = document.getElementById('upload-form');
        this.progressContainer = document.getElementById('upload-progress');
        this.progressBar = document.getElementById('progress-bar');
        
        this.initializeEventListeners();
    }

    initializeEventListeners() {
        this.dropZone.addEventListener('dragover', this.handleDragOver.bind(this));
        this.dropZone.addEventListener('dragleave', this.handleDragLeave.bind(this));
        this.dropZone.addEventListener('drop', this.handleDrop.bind(this));
        this.dropZone.addEventListener('click', () => this.fileInput.click());
        
        this.browseBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.fileInput.click();
        });
        
        this.fileInput.addEventListener('change', this.handleFileSelect.bind(this));
        
        // Prevent default drag behaviors on document
        document.addEventListener('dragover', (e) => e.preventDefault());
        document.addEventListener('drop', (e) => e.preventDefault());
    }

    handleDragOver(e) {
        e.preventDefault();
        e.stopPropagation();
        this.dropZone.classList.add('drag-over');
    }

    handleDragLeave(e) {
        e.preventDefault();
        e.stopPropagation();
        this.dropZone.classList.remove('drag-over');
    }

    handleDrop(e) {
        e.preventDefault();
        e.stopPropagation();
        this.dropZone.classList.remove('drag-over');
        
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            this.handleFile(files[0]);
        }
    }

    handleFileSelect(e) {
        const file = e.target.files[0];
        if (file) {
            this.handleFile(file);
        }
    }

    handleFile(file) {
        const allowedTypes = ['image/png', 'image/jpeg', 'image/jpg', 'image/gif', 'image/bmp', 'image/tiff', 'image/webp'];
        
        if (!allowedTypes.includes(file.type)) {
            this.showError('Invalid file type. Please select an image file.');
            return;
        }

        // Check file size (16MB limit)
        const maxSize = 16 * 1024 * 1024;
        if (file.size > maxSize) {
            this.showError('File too large. Maximum size is 16MB.');
            return;
        }

        this.uploadFile(file);
    }

    uploadFile(file) {
        const formData = new FormData(this.uploadForm);
        formData.set('file', file);
        
        this.showProgress();
        
        // Create XMLHttpRequest for better control
        const xhr = new XMLHttpRequest();
        
        xhr.upload.addEventListener('progress', (e) => {
            if (e.lengthComputable) {
                const percentComplete = (e.loaded / e.total) * 100;
                this.updateProgress(percentComplete);
            }
        });
        
        xhr.addEventListener('load', () => {
            this.hideProgress();
            
            if (xhr.status === 200) {
                // Check if response is JSON or HTML
                const contentType = xhr.getResponseHeader('Content-Type');
                
                if (contentType && contentType.includes('application/json')) {
                    try {
                        const response = JSON.parse(xhr.responseText);
                        if (response.success) {
                            this.showSuccess('Upload successful! Redirecting to analysis...');
                            setTimeout(() => {
                                window.location.href = `/analysis/${response.filename}`;
                            }, 1500);
                        } else {
                            this.showError(response.error || 'Upload failed');
                        }
                    } catch (e) {
                        this.showError('Server response error. Please try again.');
                    }
                } else {
                    // HTML response - likely a redirect or form submission
                    this.showSuccess('Upload successful!');
                    // If it's HTML, the server might have redirected, so reload the page
                    setTimeout(() => {
                        window.location.reload();
                    }, 1000);
                }
            } else {
                try {
                    const response = JSON.parse(xhr.responseText);
                    this.showError(response.error || 'Upload failed');
                } catch (e) {
                    this.showError(`Upload failed with status: ${xhr.status}`);
                }
            }
        });
        
        xhr.addEventListener('error', () => {
            this.hideProgress();
            this.showError('Network error. Please check your connection and try again.');
        });
        
        xhr.open('POST', '/upload');
        xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
        xhr.send(formData);
    }

    showProgress() {
        this.progressContainer.style.display = 'block';
        this.updateProgress(0);
    }

    updateProgress(percent) {
        this.progressBar.style.width = percent + '%';
        this.progressBar.setAttribute('aria-valuenow', percent);
    }

    hideProgress() {
        this.progressContainer.style.display = 'none';
    }

    showSuccess(message) {
        this.dropZone.classList.add('upload-success');
        this.showAlert(message, 'success');
    }

    showError(message) {
        this.showAlert(message, 'danger');
    }

    showAlert(message, type) {
        // Remove existing alerts
        const existingAlerts = document.querySelectorAll('.alert-upload');
        existingAlerts.forEach(alert => alert.remove());
        
        const alertHtml = `
            <div class="alert alert-${type} alert-dismissible fade show alert-upload" role="alert">
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
            </div>
        `;
        
        // Insert alert at top of main container
        const container = document.querySelector('main.container');
        container.insertAdjacentHTML('afterbegin', alertHtml);
    }
}

// Initialize uploader when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    new ImageUploader();
});
function previewReport() {
    try {
        var analysisData = JSON.parse('{{ analysis|tojson|safe }}');
        var report = generateHumanReadableReport(analysisData, '{{ filename }}');
        
        // Create modal to show preview
        var modal = document.createElement('div');
        modal.className = 'modal fade';
        modal.innerHTML = `
            <div class="modal-dialog modal-lg">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">PicIntel Report Preview</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <pre style="font-size: 12px; max-height: 500px; overflow-y: auto; background: #f8f9fa; padding: 15px;">${report}</pre>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
                        <button type="button" class="btn btn-success" onclick="downloadReport(); bootstrap.Modal.getInstance(this.closest('.modal')).hide();">
                            <i class="fas fa-download me-2"></i>Download Report
                        </button>
                    </div>
                </div>
            </div>
        `;
        
        document.body.appendChild(modal);
        var bsModal = new bootstrap.Modal(modal);
        bsModal.show();
        
        // Remove modal after it's hidden
        modal.addEventListener('hidden.bs.modal', function() {
            document.body.removeChild(modal);
        });
    } catch (error) {
        alert('Preview failed: ' + error.message);
    }
}
