# DocIntel AI 📄✨

DocIntel AI is a Streamlit-based web application designed for advanced document processing, analysis, and querying. It leverages AI 🤖 and NLP 🧠 to extract insights from various document formats, perform named entity recognition (NER), and enable retrieval-augmented generation (RAG) for interactive document querying. The application supports document conversion, image extraction, entity relationship visualization, and a chat interface for querying document content.

## Features 🌟

- **Document Processing** 📚: Supports multiple file formats including PDF, DOCX, TXT, CSV, PPTX, XLSX, images (JPG, PNG, etc.), code files (PY, JSON, etc.), and archives (ZIP, TAR, etc.).
- **File Conversion** 🔄: Converts documents to formats like TXT, JSON, MD, HTML, XML, DOCX, PPTX, ZIP, TAR, GZ, RAR, 7Z, and PDF with robust error handling.
- **Image Extraction** 🖼️: Extracts and displays images from documents with watermarking options and metadata (dimensions, source, format).
- **Named Entity Recognition (NER)** 🔍: Identifies entities (e.g., PERSON, ORGANIZATION, GPE) and visualizes them in a colorful, structured table and relationship graph.
- **Retrieval-Augmented Generation (RAG)** 💬: Enables users to chat with documents, answering queries with context-aware responses, including:
  - 📝 Summaries for "summary" queries.
  - 📋 Key topics for "important topics" or "index" queries using YAKE keyword extraction.
  - ✅ Examples for "examples" queries.
  - 🏷️ Named entities for queries like "find name, place, animal, thing" in a structured format.
- **Interactive UI** 🖥️: Features a responsive Streamlit interface with a welcome screen, sidebar navigation, and a colorful particle animation 🎉 for file upload/conversion.
- **Error Handling** 🚨: Comprehensive logging and user-friendly error messages for robust operation.

## Prerequisites ⚙️

- **Python** 🐍: Version 3.8 or higher.
- **Tesseract OCR** 📖: Required for OCR functionality (install via `tesseract-ocr` on your system).
- **API Key** 🔑: A Together API key for the language model (set as `TOGETHER_API_KEY` environment variable).

## Installation 🛠️

1. **Clone the Repository** (if applicable) 📂:
   ```bash
   git clone https://github.com/your-repo/docintel-ai.git
   cd docintel-ai
   ```

2. **Create a Virtual Environment** (recommended) 🌐:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install Dependencies** 📦:
   ```bash
   pip install -r requirements.txt
   ```

4. **Install spaCy Model** 🧠:
   ```bash
   python -m spacy download en_core_web_sm
   ```

5. **Set Environment Variables** 🔧:
   - Set the Together API key:
     ```bash
     export TOGETHER_API_KEY=your_api_key  # On Windows: set TOGETHER_API_KEY=your_api_key
     ```
   - Optionally, set the Tesseract path if not in system PATH:
     ```bash
     export TESSERACT_PATH=/path/to/tesseract  # e.g., /usr/bin/tesseract
     ```

## Usage 🚀

1. **Run the Application** ▶️:
   ```bash
   streamlit run docintel.py
   ```

2. **Access the Web Interface** 🌐:
   - Open your browser and navigate to `http://localhost:8501`.
   - On the welcome screen, click "Get Started" 🎉 to access the main interface.

3. **Features Overview** 🗂️:
   - **Document Chat** 💬: Upload documents or enter URLs, process them, and ask questions (e.g., "Summarize the document," "List important topics," "Find examples," "Find names and places").
   - **File Converter** 🔄: Upload a file and convert it to formats like TXT, JSON, PDF, etc.
   - **Entity Analysis** 🔎: View extracted entities, their relationships (visualized as a graph), and statistics.

## Project Structure 📑

- `docintel.py`: Main application script containing all functionality.
- `requirements.txt`: List of Python dependencies.
- `README.md`: This documentation file.

## Dependencies 📚

See `requirements.txt` for a complete list. Key dependencies include:
- Streamlit for the web interface 🖥️.
- LangChain for RAG pipeline and embeddings 🧠.
- spaCy for NER 🔍.
- YAKE for keyword extraction 📋.
- PyMuPDF, python-docx, openpyxl, etc., for document processing 📄.
- Tesseract for OCR 📖.

## Notes 📝

- Ensure Tesseract is installed and accessible in your system PATH or set `TESSERACT_PATH`.
- The application requires a stable internet connection for URL-based document loading and Together API calls 🌐.
- For large documents, processing time may vary based on system resources ⏳.
- The particle animation is optimized for modern browsers (Chrome, Firefox, Safari) 🌈.

## Troubleshooting 🛠️

- **Tesseract Not Found** 🚫: Install Tesseract (`sudo apt-get install tesseract-ocr` on Ubuntu, or equivalent) and set `TESSERACT_PATH` if needed.
- **API Key Issues** 🔑: Verify your Together API key is valid and set correctly.
- **Dependency Errors** ⚠️: Ensure all packages in `requirements.txt` are installed correctly. Use a virtual environment to avoid conflicts.
- **File Conversion Failures** 📉: Check file integrity and supported formats. Review logs for specific errors.

## Contributing 🤝

Contributions are welcome! Please submit a pull request or open an issue on the repository. 🌟

## License 📜

This project is licensed under the MIT License.