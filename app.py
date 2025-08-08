import os
import sys
import tempfile
import re
import logging
import html
from typing import List, Dict, Optional, Union, Any
from pathlib import Path
from dotenv import load_dotenv
import requests
from pypdf import PdfReader
from docx import Document as DocxDocument
from openpyxl import load_workbook
from pptx import Presentation
import pandas as pd
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
import pytesseract
import numpy as np
import fitz  # PyMuPDF
# removed duplicate spacy import below; load dynamically
import networkx as nx
import matplotlib.pyplot as plt
from io import BytesIO
import json
import base64

# Optional / potentially heavy or system-dependent imports:
try:
    from odf import text, teletype
    from odf.opendocument import load as load_odt
except Exception:
    # ODT support not available — handle gracefully at runtime
    text = None
    teletype = None
    load_odt = None

try:
    import docx2txt
except Exception:
    docx2txt = None

# Archive libs that may require system dependencies
try:
    import rarfile
except Exception:
    rarfile = None

try:
    import py7zr
except Exception:
    py7zr = None

try:
    import yake
except Exception:
    yake = None

import subprocess
from collections import Counter

# LangChain imports (keep these where you need them; may require installation)
# If any of these fail, import inside the respective function that actually uses them.
try:
    from langchain_core.documents import Document
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_community.vectorstores import FAISS
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.runnables import RunnablePassthrough
    from langchain_core.output_parsers import StrOutputParser
    from langchain_community.document_loaders import WebBaseLoader
    from langchain_together import Together
    from langchain_community.embeddings import HuggingFaceEmbeddings
except Exception:
    # Fallback to lazy import at runtime if needed
    pass

# Streamlit imports
try:
    import streamlit as st
    from streamlit_option_menu import option_menu
except Exception:
    st = None
    option_menu = None

# Suppress warnings
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

# -------------------------
# Logging configuration
# -------------------------
LOG_FILE = "docintel.log"
logger = logging.getLogger("docintel")
logger.setLevel(logging.INFO)

# Avoid multiple handlers if module reloaded
if not logger.handlers:
    fh = logging.FileHandler(LOG_FILE)
    fh.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s in %(module)s: %(message)s"
    )
    fh.setFormatter(formatter)
    sh.setFormatter(formatter)
    logger.addHandler(fh)
    logger.addHandler(sh)

# -------------------------
# Requests session with global User-Agent
# -------------------------
os.environ.setdefault("USER_AGENT", "DocIntelAI/1.0 (Python; +https://github.com/docintel-ai)")
USER_AGENT = os.environ.get("USER_AGENT", "DocIntelAI/1.0")
session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})

# -------------------------
# spaCy model loader (robust)
# -------------------------
def load_spacy_model(model_name: str = "en_core_web_sm"):
    """Load spaCy model, try to auto-download if missing, using the current interpreter."""
    try:
        import spacy
        nlp_local = spacy.load(model_name)
        logger.info(f"Loaded spaCy model {model_name}")
        return nlp_local
    except Exception as e:
        logger.warning(f"spaCy model {model_name} not available ({e}), attempting to download...")
        try:
            subprocess.run([sys.executable, "-m", "spacy", "download", model_name], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            import spacy
            nlp_local = spacy.load(model_name)
            logger.info(f"Successfully downloaded and loaded spaCy model {model_name}")
            return nlp_local
        except subprocess.CalledProcessError as ex:
            logger.error(f"Failed to download spaCy model {model_name}: {ex}")
        except Exception as ex:
            logger.error(f"Unexpected error loading spaCy model {model_name}: {ex}")
    return None

nlp = load_spacy_model("en_core_web_sm")

# -------------------------
# Helper functions
# -------------------------
def get_image_download_link(img_bytes: bytes, filename: str = "image.png", text: str = "Download") -> str:
    """Generate download link for image (HTML anchor with base64)."""
    try:
        b64 = base64.b64encode(img_bytes).decode()
        return f'<a href="data:image/png;base64,{b64}" download="{html.escape(filename)}">{html.escape(text)}</a>'
    except Exception as e:
        logger.error(f"Error generating download link: {e}")
        return ""

def create_watermarked_image(image_bytes: bytes, text: str = "DocIntel AI") -> bytes:
    """Create a watermarked version of the image. Falls back gracefully if fonts unavailable."""
    try:
        img = Image.open(BytesIO(image_bytes)).convert("RGBA")
        width, height = img.size

        # Create overlay for watermark so we can control opacity
        overlay = Image.new("RGBA", img.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)

        # Try to load a truetype font; fall back to default if not available.
        font_size = max(18, int(min(width, height) * 0.04))  # adaptive size
        try:
            # Use DejaVuSans (commonly available) then Arial
            font = ImageFont.truetype("DejaVuSans.ttf", font_size)
        except Exception:
            try:
                font = ImageFont.truetype("arial.ttf", font_size)
            except Exception:
                font = ImageFont.load_default()

        # Compute text size
        try:
            text_bbox = draw.textbbox((0, 0), text, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
        except Exception:
            text_width, text_height = draw.textsize(text, font=font)

        padding = int(font_size * 0.4)
        x = width - text_width - padding
        y = height - text_height - padding

        # Semi-transparent white text with slight black shadow for visibility
        shadow_position = (x + 1, y + 1)
        draw.text(shadow_position, text, font=font, fill=(0, 0, 0, 120))
        draw.text((x, y), text, font=font, fill=(255, 255, 255, 180))

        # Composite overlay onto original image
        watermarked = Image.alpha_composite(img, overlay).convert("RGB")
        buf = BytesIO()
        watermarked.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        logger.error(f"Error creating watermarked image: {e}")
        # Return original bytes if watermarking fails
        return image_bytes

# ---- Document Processing ----
class DocumentProcessor:
    """Handles document conversion and processing with image extraction."""
    
    @staticmethod
    def get_supported_formats() -> Dict[str, List[str]]:
        """Return supported file formats."""
        return {
            "📄 Documents": [".pdf", ".docx", ".txt", ".doc", ".rtf", ".odt", ".tex"],
            "📊 Spreadsheets": [".xlsx", ".csv", ".xls", ".tsv", ".ods", ".xlsm"],
            "🖼️ Images": [".jpg", ".jpeg", ".png", ".tiff", ".bmp"],
            "💻 Code": [".py", ".json", ".html", ".js", ".css", ".java"],
            "📦 Archives": [".zip", ".tar", ".gz", ".rar", ".7z"]
        }
    
    @staticmethod
    def extract_images_from_pdf(pdf_path: str) -> List[Dict[str, Any]]:
        """Extract images from PDF file with metadata and deduplication."""
        images = []
        seen_hashes = set()
        try:
            with fitz.open(pdf_path) as pdf:
                for page_num in range(len(pdf)):
                    page = pdf.load_page(page_num)
                    for img in page.get_images(full=True):
                        xref = img[0]
                        base_image = pdf.extract_image(xref)
                        image_bytes = base_image["image"]
                        image_hash = hash(image_bytes)
                        if image_hash not in seen_hashes:
                            images.append({
                                "bytes": BytesIO(image_bytes),
                                "source": f"Page {page_num+1}",
                                "format": base_image["ext"],
                                "width": base_image["width"],
                                "height": base_image["height"]
                            })
                            seen_hashes.add(image_hash)
        except Exception as e:
            logger.error(f"Error extracting images from PDF: {str(e)}")
        return images
    
    @staticmethod
    def extract_images_from_docx(docx_path: str) -> List[Dict[str, Any]]:
        """Extract images from DOCX file with metadata and deduplication."""
        images = []
        seen_hashes = set()
        try:
            doc = DocxDocument(docx_path)
            for rel in doc.part.rels.values():
                if "image" in rel.target_ref:
                    img_data = rel.target_part.blob
                    image_hash = hash(img_data)
                    if image_hash not in seen_hashes:
                        with Image.open(BytesIO(img_data)) as img:
                            images.append({
                                "bytes": BytesIO(img_data),
                                "source": "Document",
                                "format": img.format,
                                "width": img.width,
                                "height": img.height
                            })
                            seen_hashes.add(image_hash)
        except Exception as e:
            logger.error(f"Error extracting images from DOCX: {str(e)}")
        return images
    
    @staticmethod
    def extract_images(file_path: str) -> List[Dict[str, Any]]:
        """Extract images from supported file types with metadata and deduplication."""
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            return DocumentProcessor.extract_images_from_pdf(file_path)
        elif ext in [".docx", ".doc"]:
            return DocumentProcessor.extract_images_from_docx(file_path)
        elif ext in [".jpg", ".jpeg", ".png", ".tiff", ".bmp"]:
            img_data = open(file_path, "rb").read()
            return [{
                "bytes": BytesIO(img_data),
                "source": "Image",
                "format": ext[1:].upper(),
                "width": Image.open(file_path).width,
                "height": Image.open(file_path).height
            }]
        return []
    
    @staticmethod
    def convert_to_text(file_path: str, target_format: str = "txt") -> Optional[Union[str, BytesIO]]:
        """Convert document to text or other formats with enhanced OCR handling."""
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return None
        
        try:
            ext = os.path.splitext(file_path)[1].lower()
            text = ""
            
            if ext == ".pdf":
                with fitz.open(file_path) as pdf:
                    text = "\n".join([page.get_text() for page in pdf])
                    if not text.strip():
                        text = DocumentProcessor._pdf_ocr(file_path)
            elif ext in [".docx", ".doc"]:
                text = docx2txt.process(file_path)
            elif ext == ".rtf":
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            elif ext == ".odt":
                doc = load_odt(file_path)
                text = teletype.extractText(doc)
            elif ext == ".tex":
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                text = re.sub(r'%.*?\n', '', text)
                text = re.sub(r'\\[a-zA-Z]+{', '{', text)
            elif ext in [".xlsx", ".xls", ".xlsm", ".ods"]:
                wb = load_workbook(file_path, read_only=True)
                text = ""
                for sheet in wb.sheetnames:
                    text += f"Sheet: {sheet}\n"
                    for row in wb[sheet].iter_rows(values_only=True):
                        text += "\t".join(str(cell) for cell in row if cell is not None) + "\n"
            elif ext == ".pptx":
                prs = Presentation(file_path)
                text = ""
                for slide in prs.slides:
                    for shape in slide.shapes:
                        if hasattr(shape, "text"):
                            text += shape.text + "\n"
            elif ext in [".jpg", ".jpeg", ".png", ".tiff", ".bmp"]:
                text = DocumentProcessor._enhanced_ocr(file_path)
            elif ext == ".csv":
                try:
                    df = pd.read_csv(file_path, encoding="utf-8", encoding_errors="ignore")
                    if df.empty:
                        logger.error(f"CSV file is empty")
                        return None
                    text = df.to_string()
                except Exception as e:
                    logger.error(f"Error reading CSV: {str(e)}")
                    return None
            elif ext == ".tsv":
                df = pd.read_csv(file_path, sep="\t", encoding="utf-8", encoding_errors="ignore")
                text = df.to_string()
            else:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            
            text = DocumentProcessor.clean_text(text)
            
            if target_format == "txt":
                return text
            elif target_format == "json":
                return json.dumps({"content": text}, ensure_ascii=False)
            elif target_format == "md":
                return f"```text\n{text}\n```"
            elif target_format == "html":
                return f"<pre>{html.escape(text)}</pre>"
            elif target_format == "xml":
                return f'<?xml version="1.0" encoding="UTF-8"?>\n<document>\n<content>{html.escape(text)}</content>\n</document>'
            elif target_format == "docx":
                try:
                    doc = DocxDocument()
                    doc.add_paragraph(text)
                    buf = BytesIO()
                    doc.save(buf)
                    buf.seek(0)
                    return buf
                except Exception as e:
                    logger.error(f"Error converting to DOCX: {str(e)}")
                    return None
            elif target_format == "pdf":
                try:
                    buf = BytesIO()
                    c = canvas.Canvas(buf, pagesize=letter)
                    text_lines = text.split("\n")
                    y = 750
                    for line in text_lines:
                        if y < 50:
                            c.showPage()
                            y = 750
                        c.drawString(50, y, line[:100])
                        y -= 15
                    c.save()
                    buf.seek(0)
                    return buf
                except Exception as e:
                    logger.error(f"Error converting to PDF: {str(e)}")
                    return None
            
            logger.error(f"Unsupported target format: {target_format}")
            return None
        except Exception as e:
            logger.error(f"Error converting file to {target_format}: {str(e)}")
            return None
    
    @staticmethod
    def _enhanced_ocr(image_path: str) -> str:
        """Perform enhanced OCR with improved image preprocessing."""
        try:
            if not pytesseract.get_tesseract_version():
                logger.error("Tesseract not found in PATH")
                return "OCR unavailable: Tesseract not installed"
            
            img = Image.open(image_path)
            img = img.convert('L')
            img = ImageEnhance.Contrast(img).enhance(2.0)
            img = ImageEnhance.Sharpness(img).enhance(2.0)
            img = Image.eval(img, lambda x: 0 if x < 128 else 255)
            custom_config = r'--oem 3 --psm 6 -l eng'
            text = pytesseract.image_to_string(img, config=custom_config)
            return text.strip() if text.strip() else "No text detected in image"
        except Exception as e:
            logger.error(f"OCR failed for image: {str(e)}")
            return f"OCR failed: {str(e)}"
    
    @staticmethod
    def _pdf_ocr(pdf_path: str) -> str:
        """Perform OCR on PDF pages with enhanced preprocessing."""
        text = ""
        try:
            if not pytesseract.get_tesseract_version():
                logger.error("Tesseract not found in PATH")
                return "OCR unavailable: Tesseract not installed"
            
            with fitz.open(pdf_path) as pdf:
                for page_num in range(len(pdf)):
                    pix = pdf[page_num].get_pixmap(matrix=fitz.Matrix(2, 2))
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                        img.save(tmp.name)
                        page_text = DocumentProcessor._enhanced_ocr(tmp.name)
                        text += f"\nPage {page_num+1}:\n{page_text}\n"
                    os.remove(tmp.name)
        except Exception as e:
            logger.error(f"PDF OCR failed: {str(e)}")
            return f"OCR failed: {str(e)}"
        return text
    
    @staticmethod
    def clean_text(text: str) -> str:
        """Clean text to remove artifacts."""
        text = re.sub(r'[^\x20-\x7E\n\t\r]', '', text)
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n\s+\n', '\n\n', text)
        return text.strip()

# ---- Document Loader ----
class DocumentLoader:
    """Loads documents from various sources."""
    
    @staticmethod
    def load_documents(file_paths: List[str]) -> List[Document]:
        """Load documents from file paths or URLs with improved error handling."""
        documents = []
        
        for file_path in file_paths:
            try:
                if file_path.startswith(('http://', 'https://')):
                    headers = {"User-Agent": USER_AGENT}
                    loader = WebBaseLoader(file_path, header_template=headers, verify_ssl=True)
                    docs = loader.load()
                    if not docs:
                        logger.warning(f"No content extracted from URL: {file_path}")
                        continue
                    # Clean and validate URL content
                    for doc in docs:
                        doc.page_content = DocumentProcessor.clean_text(doc.page_content)
                        if doc.page_content.strip():
                            documents.append(doc)
                    continue
                
                text = DocumentProcessor.convert_to_text(file_path)
                if text and text.strip():
                    metadata = {"source": os.path.basename(file_path)}
                    documents.append(Document(page_content=text, metadata=metadata))
                else:
                    logger.error(f"No content extracted from {file_path}")
            except Exception as e:
                logger.error(f"Error loading document {file_path}: {str(e)}")
        
        return documents

# ---- NER Analyzer ----
class NERAnalyzer:
    """Performs Named Entity Recognition and relationship analysis."""
    
    @staticmethod
    def analyze_documents(documents: List[Document]) -> Dict[str, Any]:
        """Analyze documents for named entities and relationships."""
        combined_text = "\n\n".join([doc.page_content for doc in documents])
        doc = nlp(combined_text)
        
        entities = []
        seen_entities = set()
        for ent in doc.ents:
            entity_key = (ent.text, ent.label_)
            if entity_key not in seen_entities:
                entities.append({
                    "text": ent.text,
                    "label": ent.label_,
                    "start": ent.start_char,
                    "end": ent.end_char
                })
                seen_entities.add(entity_key)
        
        graph = NERAnalyzer._build_entity_graph(doc)
        
        return {
            "entities": entities,
            "graph": graph,
            "raw_text": combined_text
        }
    
    @staticmethod
    def _build_entity_graph(doc) -> nx.Graph:
        """Build a graph of entity relationships."""
        graph = nx.Graph()
        
        for ent in doc.ents:
            graph.add_node(ent.text, type=ent.label_)
        
        for sent in doc.sents:
            sent_ents = list(set([ent.text for ent in sent.ents]))
            for i, ent1 in enumerate(sent_ents):
                for ent2 in sent_ents[i+1:]:
                    if graph.has_edge(ent1, ent2):
                        graph[ent1][ent2]["weight"] += 1
                    else:
                        graph.add_edge(ent1, ent2, weight=1)
        
        return graph
    
    @staticmethod
    def visualize_entities(entities: List[Dict]) -> str:
        """Generate HTML table visualization of entities with enhanced styling."""
        if not entities:
            return "<p style='color: #333; font-size: 1.2rem;'>No entities found</p>"
        
        entity_types = {}
        for entity in entities:
            if entity["label"] not in entity_types:
                entity_types[entity["label"]] = []
            entity_types[entity["label"]].append(entity["text"])
        
        color_map = {
            "PERSON": "#FFD700",
            "ORG": "#87CEEB",
            "GPE": "#98FB98",
            "DATE": "#FFA07A",
            "NORP": "#DDA0DD",
            "CARDINAL": "#FFB6C1",
            "PRODUCT": "#FF69B4",
            "PERCENT": "#FF4500",
            "ORDINAL": "#ADFF2F",
            "OTHER": "#CCCCCC"
        }
        
        html_content = "<div class='entity-container'>"
        html_content += "<h2 style='color: #333; margin-bottom: 20px;'>Named Entities</h2>"
        html_content += "<table class='entity-table'>"
        html_content += "<tr><th style='background-color: #4facfe;'>Entity Type</th><th style='background-color: #4facfe;'>Entity Text</th></tr>"
        
        for label in sorted(entity_types.keys()):
            color = color_map.get(label, color_map["OTHER"])
            for text in sorted(set(entity_types[label])):
                html_content += f"<tr style='background-color: {color};'><td>{label.upper()}</td><td>{html.escape(text)}</td></tr>"
        
        html_content += "</table></div>"
        return html_content
    
    @staticmethod
    def visualize_graph(graph: nx.Graph) -> Optional[BytesIO]:
        """Visualize the entity graph with enhanced styling."""
        if not graph.nodes():
            return None
        
        plt.figure(figsize=(14, 10))
        
        node_colors = []
        for node in graph.nodes():
            node_type = graph.nodes[node].get("type", "OTHER")
            colors = {
                "PERSON": "#FFD700",
                "ORG": "#87CEEB",
                "GPE": "#98FB98",
                "DATE": "#FFA07A",
                "NORP": "#DDA0DD",
                "CARDINAL": "#FFB6C1",
                "PRODUCT": "#FF69B4",
                "PERCENT": "#FF4500",
                "ORDINAL": "#ADFF2F"
            }
            node_colors.append(colors.get(node_type, "#FFB6C1"))
        
        pos = nx.spring_layout(graph, k=0.8, iterations=50)
        edge_weights = [graph[u][v]["weight"] for u, v in graph.edges()]
        max_weight = max(edge_weights, default=1)
        edge_widths = [3 * (w / max_weight) for w in edge_weights]
        nx.draw_networkx_nodes(graph, pos, node_size=1000, node_color=node_colors, alpha=0.9)
        nx.draw_networkx_edges(graph, pos, width=edge_widths, alpha=0.6)
        
        labels = {n: n for n in graph.nodes()}
        nx.draw_networkx_labels(graph, pos, labels, font_size=10, font_weight="bold")
        
        edge_labels = nx.get_edge_attributes(graph, 'weight')
        nx.draw_networkx_edge_labels(graph, pos, edge_labels=edge_labels, font_size=8)
        
        plt.title("Entity Relationship Graph", fontsize=16, pad=20)
        plt.axis("off")
        
        buf = BytesIO()
        plt.savefig(buf, format="png", bbox_inches="tight", dpi=150)
        plt.close()
        buf.seek(0)
        return buf

# ---- RAG Pipeline ----
class RAGPipeline:
    """Retrieval-Augmented Generation pipeline."""
    
    def __init__(self):
        self.vectorstore = None
        self.llm = None
        self.retriever = None
        self.embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-mpnet-base-v2")
    
    def create_vectorstore(self, documents: List[Document]) -> None:
        """Create vector store from documents."""
        try:
            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=1500,
                chunk_overlap=300
            )
            splits = text_splitter.split_documents(documents)
            if not splits:
                raise ValueError("No document chunks created")
            self.vectorstore = FAISS.from_documents(splits, self.embeddings)
            self.retriever = self.vectorstore.as_retriever(search_type="similarity_score_threshold", search_kwargs={"score_threshold": 0.3, "k": 5})
        except Exception as e:
            logger.error(f"Error creating vectorstore: {str(e)}")
            raise
    
    def set_llm(self) -> None:
        """Initialize the language model."""
        try:
            api_key = os.getenv("TOGETHER_API_KEY")
            if not api_key:
                raise ValueError("TOGETHER_API_KEY environment variable not set")
            self.llm = Together(
                model="mistralai/Mistral-7B-Instruct-v0.1",
                temperature=0.7,
                max_tokens=1024,
                top_k=50,
                top_p=0.9,
                together_api_key=api_key
            )
        except Exception as e:
            logger.error(f"Error initializing LLM: {str(e)}")
            raise
    
    def _classify_query(self, question: str) -> tuple[str, bool]:
        """Classify the query type and check if image analysis is requested."""
        question = question.lower().strip()
        is_image_query = any(keyword in question for keyword in ["image", "picture", "scan the image"])
        
        if "summary" in question or "summarize" in question:
            return "summary", is_image_query
        elif "important topics" in question or "index" in question or "key topics" in question:
            return "topics", is_image_query
        elif "example" in question or "examples" in question:
            return "examples", is_image_query
        elif any(word in question for word in ["name", "place", "animal", "thing"]):
            return "entities", is_image_query
        elif "question number" in question or "question" in question:
            return "question", is_image_query
        return "general", is_image_query
    
    def _extract_topics(self, context: str) -> List[str]:
        """Extract key topics using YAKE keyword extraction."""
        try:
            kw_extractor = yake.KeywordExtractor(lan="en", n=3, dedupLim=0.9, top=10, features=None)
            keywords = kw_extractor.extract_keywords(context)
            return [kw[0] for kw in keywords]
        except Exception as e:
            logger.error(f"Error extracting topics: {str(e)}")
            return []
    
    def _extract_examples(self, context: str) -> List[str]:
        """Extract examples from the context."""
        examples = []
        sentences = context.split("\n")
        for sentence in sentences:
            if "example" in sentence.lower() or "for instance" in sentence.lower() or "e.g." in sentence.lower():
                examples.append(sentence.strip())
        return examples if examples else ["No examples found in the document."]
    
    def _extract_entities(self, context: str, query: str) -> List[Dict]:
        """Extract named entities based on query terms."""
        doc = nlp(context)
        entities = []
        query_lower = query.lower()
        wanted_labels = []
        if "name" in query_lower:
            wanted_labels.append("PERSON")
        if "place" in query_lower:
            wanted_labels.append("GPE")
        if "animal" in query_lower:
            wanted_labels.append("NORP")
        if "thing" in query_lower:
            wanted_labels.extend(["PRODUCT", "OBJECT"])
        
        for ent in doc.ents:
            if not wanted_labels or ent.label_ in wanted_labels:
                if ent.label_ == "PERSON" and len(ent.text.split()) > 2:
                    continue
                entities.append({"text": ent.text, "label": ent.label_})
        return entities if entities else [{"text": "No relevant entities found", "label": "N/A"}]
    
    def _analyze_images(self, images: List[Dict[str, Any]], question: str = "") -> str:
        """Analyze images and return OCR results with metadata."""
        result = "## Image Analysis\n"
        question_num = None
        if "question number" in question.lower():
            try:
                question_num = int(re.search(r'\d+', question).group())
            except:
                pass
        
        found = False
        for i, img_info in enumerate(images):
            try:
                img_bytes = img_info["bytes"].getvalue()
                img = Image.open(BytesIO(img_bytes))
                ocr_text = DocumentProcessor._enhanced_ocr(BytesIO(img_bytes))
                if question_num and f"question {question_num}" in ocr_text.lower():
                    found = True
                    result += f"### Image for Question {question_num}\n"
                    result += f"- **Source**: {img_info['source']}\n"
                    result += f"- **Dimensions**: {img_info['width']}x{img_info['height']} pixels\n"
                    result += f"- **Format**: {img_info['format']}\n"
                    result += f"- **Extracted Text**: {ocr_text if ocr_text else 'No text detected'}\n\n"
                    return result
                result += f"### Image {i+1} Details\n"
                result += f"- **Source**: {img_info['source']}\n"
                result += f"- **Dimensions**: {img_info['width']}x{img_info['height']} pixels\n"
                result += f"- **Format**: {img_info['format']}\n"
                result += f"- **Extracted Text**: {ocr_text if ocr_text else 'No text detected'}\n\n"
            except Exception as e:
                logger.error(f"Error analyzing image {i}: {str(e)}")
                result += f"### Image {i+1}\n- **Error**: Unable to analyze image\n\n"
        
        if question_num and not found:
            result += f"**No image found for Question {question_num}. Would you like to know more about it?**\n"
        return result
    
    def generate_answer(self, question: str, images: List[Dict[str, Any]] = None, raw_documents: List[Document] = None) -> Dict[str, Any]:
        """Generate answer using RAG pipeline with image or document analysis."""
        if not self.retriever or not self.llm:
            raise ValueError("Vectorstore and LLM must be initialized")
        
        query_type, is_image_query = self._classify_query(question)
        docs = self.retriever.invoke(question) if not is_image_query else []
        context = "\n\n".join(doc.page_content for doc in docs) if docs else ""
        
        # Fallback to raw document content if retriever finds no matches
        if not context and not is_image_query and raw_documents:
            context = "\n\n".join(doc.page_content for doc in raw_documents)[:10000]  # Limit for performance
        
        try:
            with st.spinner("Analyzing..." if is_image_query else "Processing documents..."):
                answer = ""
                if is_image_query and images:
                    answer += self._analyze_images(images, question)
                    if query_type != "general":
                        context = answer  # Use image OCR text as context for specific queries
                
                if not context and not is_image_query:
                    return {
                        "answer": "**No relevant information found in the file. Would you like to know more about it?**",
                        "sources": []
                    }
                
                if query_type == "summary":
                    template = """
                    You are an expert assistant specializing in document analysis. Provide a clear and concise summary of the content in markdown format, using bullet points for key points and avoiding technical jargon. Summarize all available content, even if it's brief. If no meaningful content is found, respond with: "No meaningful content found in the file. Would you like to know more about it?"

                    Content:
                    {context}

                    Answer:
                    """
                    prompt = ChatPromptTemplate.from_template(template)
                    chain = (
                        {"context": lambda x: context, "question": RunnablePassthrough()}
                        | prompt
                        | self.llm
                        | StrOutputParser()
                    )
                    response = chain.invoke(question)
                    answer += f"## Summary\n{response}" if response.strip() and "no meaningful content" not in response.lower() else "**No meaningful content found in the file. Would you like to know more about it?**"
                
                elif query_type == "topics":
                    topics = self._extract_topics(context)
                    answer += "## Key Topics\n" + "\n".join([f"- {topic}" for topic in topics]) if topics else "**No relevant topics found in the file. Would you like to know more about it?**"
                
                elif query_type == "examples":
                    examples = self._extract_examples(context)
                    answer += "## Examples\n" + "\n".join([f"- {example}" for example in examples]) if examples and examples != ["No examples found in the document."] else "**No examples found in the file. Would you like to know more about it?**"
                
                elif query_type == "entities":
                    entities = self._extract_entities(context, question)
                    if entities and entities != [{"text": "No relevant entities found", "label": "N/A"}]:
                        answer += "## Named Entities\n| **Entity** | **Type** |\n|------------|----------|\n"
                        for ent in entities:
                            answer += f"| {ent['text']} | {ent['label']} |\n"
                    else:
                        answer += "**No relevant entities found in the file. Would you like to know more about it?**"
                
                elif query_type == "question":
                    template = """
                    You are an expert assistant specializing in document analysis. Answer the question in markdown format, focusing on the specific question number or topic requested. Use structured headings, subheadings, and bullet points for clarity. If the question references a specific question number, extract and summarize only the relevant information. If no relevant information is found, respond with: "No relevant information found in the file for this question. Would you like to know more about it?"

                    Content:
                    {context}

                    Question: {question}

                    Answer:
                    """
                    prompt = ChatPromptTemplate.from_template(template)
                    chain = (
                        {"context": lambda x: context, "question": RunnablePassthrough()}
                        | prompt
                        | self.llm
                        | StrOutputParser()
                    )
                    response = chain.invoke(question)
                    answer += response if response.strip() and "no relevant information" not in response.lower() else f"**No relevant information found in the file for this question. Would you like to know more about it?**"
                
                else:
                    template = """
                    You are an expert assistant specializing in document analysis. Answer the question in markdown format with clear headings, subheadings, and bullet points or tables for clarity. Avoid technical jargon and file paths. If no relevant information is found, respond with: "No relevant information found in the file. Would you like to know more about it?"

                    Content:
                    {context}

                    Question: {question}

                    Answer:
                    """
                    prompt = ChatPromptTemplate.from_template(template)
                    chain = (
                        {"context": lambda x: context, "question": RunnablePassthrough()}
                        | prompt
                        | self.llm
                        | StrOutputParser()
                    )
                    response = chain.invoke(question)
                    answer += response if response.strip() and "no relevant information" not in response.lower() else "**No relevant information found in the file. Would you like to know more about it?**"
                
                return {
                    "answer": answer.strip(),
                    "sources": []
                }
        except Exception as e:
            logger.error(f"Error generating answer: {str(e)}")
            return {
                "answer": f"**Sorry, I couldn't process your request: {str(e)}. Please try again.**",
                "sources": []
            }

# ---- Main App ----
def main():
    st.set_page_config(page_title="DocIntel AI", page_icon="📄")
    
    if "show_welcome" not in st.session_state:
        st.session_state.show_welcome = True
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    if "processed_docs" not in st.session_state:
        st.session_state.processed_docs = False
    if "vectorstore" not in st.session_state:
        st.session_state.vectorstore = None
    if "documents" not in st.session_state:
        st.session_state.documents = []
    if "ner_results" not in st.session_state:
        st.session_state.ner_results = None
    if "conversion_result" not in st.session_state:
        st.session_state.conversion_result = None
    if "extracted_images" not in st.session_state:
        st.session_state.extracted_images = []

    # Custom CSS with enhanced welcome section
    st.markdown("""
    <style>
    .stApp {
        background: linear-gradient(135deg, #1e3a8a 0%, #3b82f6 100%);
        color: white;
    }
    .sidebar .sidebar-content {
        background: rgba(255, 255, 255, 0.9) !important;
        backdrop-filter: blur(10px);
        border-right: 1px solid rgba(255, 255, 255, 0.2);
    }
    .stButton>button {
        background: linear-gradient(to right, #4facfe 0%, #00f2fe 100%);
        color: white;
        border: none;
        border-radius: 25px;
        padding: 12px 30px;
        font-weight: 600;
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
        transition: all 0.3s ease;
    }
    .stButton>button:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 20px rgba(0, 0, 0, 0.3);
        background: linear-gradient(to right, #60a5fa 0%, #22d3ee 100%);
    }
    .stTextInput>div>div>input {
        background: rgba(255, 255, 255, 0.9);
        border-radius: 12px;
        padding: 12px;
        border: 1px solid rgba(255, 255, 255, 0.3);
        color: #333;
    }
    .stChatMessage {
        border-radius: 18px !important;
        padding: 16px !important;
        margin-bottom: 16px !important;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
    }
    .user-message {
        background: linear-gradient(to right, #4facfe 0%, #00f2fe 100%);
        color: white;
    }
    .assistant-message {
        background: white;
        color: #333;
    }
    .document-preview {
        background: rgba(255, 255, 255, 0.9);
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 20px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
    }
    .entity-container {
        background: white;
        padding: 20px;
        border-radius: 12px;
        margin-bottom: 20px;
    }
    .entity-table {
        width: 100%;
        border-collapse: collapse;
        margin-top: 10px;
    }
    .entity-table th, .entity-table td {
        border: 1px solid #ddd;
        padding: 8px;
        text-align: left;
    }
    .entity-table th {
        background-color: #4facfe;
        color: white;
    }
    .welcome-container {
        animation: fadeIn 2s ease-in-out;
    }
    .welcome-title {
        animation: slideIn 1.5s ease-out;
    }
    .welcome-subtitle {
        animation: slideIn 1.8s ease-out;
    }
    .welcome-text {
        animation: slideIn 2s ease-out;
    }
    @keyframes fadeIn {
        from { opacity: 0; }
        to { opacity: 1; }
    }
    @keyframes slideIn {
        from { transform: translateY(20px); opacity: 0; }
        to { transform: translateY(0); opacity: 1; }
    }
    </style>
    """, unsafe_allow_html=True)

    if st.session_state.show_welcome:
        st.markdown("""
        <div class="welcome-container" style="display: flex; flex-direction: column; align-items: center; justify-content: center; height: 80vh; text-align: center;">
            <h1 class="welcome-title" style="font-size: 3.8rem; font-weight: 800; color: #ffffff; margin-bottom: 1rem; text-shadow: 0 4px 12px rgba(0,0,0,0.3);">DocIntel AI</h1>
            <h3 class="welcome-subtitle" style="font-size: 1.9rem; font-weight: 400; color: #f0f0f0; margin-bottom: 1.5rem;">Advanced RAG-Powered Document Analysis</h3>
            <p class="welcome-text" style="font-size: 1.3rem; color: #e0e0e0; max-width: 700px; line-height: 1.6; margin-bottom: 2rem;">
                Effortlessly analyze documents, images, and web content with our state-of-the-art Retrieval-Augmented Generation technology. Upload files or URLs to extract insights, summarize content, and explore entities with precision.
            </p>
            <ul style="font-size: 1.1rem; color: #e0e0e0; text-align: left; max-width: 500px; margin-bottom: 2rem;">
                <li>📄 Upload PDFs, Word documents, images, and more</li>
                <li>🌐 Analyze web content via URLs</li>
                <li>💬 Ask questions and get precise answers</li>
                <li>🔍 Explore named entities and relationships</li>
            </ul>
        </div>
        """, unsafe_allow_html=True)
        
        col1, col2, col3 = st.columns([1,2,1])
        with col2:
            if st.button("Start Analyzing", key="enter-app", use_container_width=True):
                st.session_state.show_welcome = False
                st.rerun()
        return

    with st.sidebar:
        st.title("DocIntel AI")
        
        selected = option_menu(
            menu_title=None,
            options=["Document Chat", "File Converter", "Entity Analysis"],
            icons=["chat", "file-earmark-arrow-down", "diagram-3"],
            default_index=0,
            styles={
                "container": {"background-color": "rgba(255,255,255,0.8)"},
                "nav-link": {"color": "#333", "font-weight": "normal"},
                "nav-link-selected": {"background-color": "#4facfe", "font-weight": "bold"}
            }
        )
        
        if selected == "Document Chat":
            uploaded_files = st.file_uploader(
                "Upload Documents",
                type=["pdf", "docx", "txt", "csv", "pptx", "xlsx", "jpg", "png", "doc", "rtf", "odt", "tex", "tsv", "xls", "ods", "xlsm", "py", "json", "html", "js", "css", "java", "zip", "tar", "gz", "rar", "7z"],
                accept_multiple_files=True,
                help="Upload supported document, image, code, or archive files"
            )
            
            url = st.text_input("Or enter a URL (e.g., https://example.com):", placeholder="https://example.com")
            
            if st.button("Process Documents", disabled=not (uploaded_files or (url and url.startswith(('http://', 'https://'))))):
                with st.spinner("Analyzing documents..."):
                    try:
                        file_paths = []
                        if uploaded_files:
                            temp_dir = tempfile.mkdtemp()
                            for uploaded_file in uploaded_files:
                                file_path = os.path.join(temp_dir, uploaded_file.name)
                                with open(file_path, "wb") as f:
                                    f.write(uploaded_file.getbuffer())
                                file_paths.append(file_path)
                        
                        if url and url.startswith(('http://', 'https://')):
                            file_paths.append(url)
                        
                        st.session_state.extracted_images = []
                        for file_path in file_paths:
                            if not file_path.startswith(('http://', 'https://')):
                                images = DocumentProcessor.extract_images(file_path)
                                st.session_state.extracted_images.extend(images)
                        
                        rag = RAGPipeline()
                        documents = DocumentLoader.load_documents(file_paths)
                        
                        if documents:
                            st.session_state.documents = documents
                            rag.create_vectorstore(documents)
                            rag.set_llm()
                            st.session_state.vectorstore = rag
                            st.session_state.processed_docs = True
                            st.success("Documents processed successfully!")
                            
                            with st.spinner("Analyzing entities..."):
                                st.session_state.ner_results = NERAnalyzer.analyze_documents(documents)
                        else:
                            st.error("No valid content found. Please upload valid files or a working URL.")
                    except Exception as e:
                        st.error(f"Error processing documents: {str(e)}")
                        logger.error(f"Error processing documents: {str(e)}")

        elif selected == "File Converter":
            conv_file = st.file_uploader(
                "Select file to convert",
                type=["pdf", "docx", "txt", "csv", "pptx", "xlsx", "jpg", "png", "doc", "rtf", "odt", "tex", "tsv", "xls", "ods", "xlsm", "py", "json", "html", "js", "css", "java", "zip", "tar", "gz", "rar", "7z"]
            )
            
            target_format = st.selectbox(
                "Convert to",
                options=["txt", "json", "md", "html", "xml", "docx", "pdf"],
                index=0
            )
            
            if st.button("Convert File", disabled=not conv_file):
                with st.spinner("Converting file..."):
                    try:
                        temp_dir = tempfile.mkdtemp()
                        file_path = os.path.join(temp_dir, conv_file.name)
                        with open(file_path, "wb") as f:
                            f.write(conv_file.getbuffer())
                        
                        st.session_state.extracted_images = DocumentProcessor.extract_images(file_path)
                        
                        converted = DocumentProcessor.convert_to_text(file_path, target_format)
                        if converted:
                            st.session_state.conversion_result = {
                                "content": converted,
                                "format": target_format,
                                "filename": f"{os.path.splitext(conv_file.name)[0]}.{target_format}"
                            }
                            st.success("File converted successfully!")
                        else:
                            st.error("Conversion failed. Please check the file and try again.")
                    except Exception as e:
                        st.error(f"Conversion error: {str(e)}")
                        logger.error(f"Conversion error: {str(e)}")

        elif selected == "Entity Analysis":
            if st.session_state.ner_results:
                st.success("Entity analysis ready for your documents")
            else:
                st.info("Please upload and process documents to analyze entities")

    if selected == "Document Chat":
        st.header("Document Chat")
        
        if st.session_state.processed_docs:
            with st.expander("Document Preview", expanded=False):
                combined_content = "\n\n".join([doc.page_content for doc in st.session_state.documents])
                st.text_area(
                    "Content Preview",
                    value=combined_content[:5000] + ("..." if len(combined_content) > 5000 else ""),
                    height=300,
                    disabled=True
                )
            
            if st.session_state.extracted_images:
                st.subheader("Extracted Images")
                for i, img_info in enumerate(st.session_state.extracted_images):
                    try:
                        img_bytes = img_info["bytes"].getvalue()
                        img = Image.open(BytesIO(img_bytes))
                        img.thumbnail((200, 200))
                        watermarked_img = create_watermarked_image(img_bytes)
                        
                        with st.expander(f"Image {i+1} - {img_info['source']}", expanded=False):
                            col1, col2 = st.columns(2)
                            with col1:
                                st.image(img, caption="Original", use_container_width=True)
                            with col2:
                                st.image(watermarked_img, caption="Watermarked", use_container_width=True)
                            
                            col1, col2 = st.columns(2)
                            with col1:
                                st.download_button(
                                    label="Download Original",
                                    data=img_bytes,
                                    file_name=f"image_{i+1}_original.{img_info['format'].lower()}",
                                    mime=f"image/{img_info['format'].lower()}",
                                    key=f"dl_orig_{i}"
                                )
                            with col2:
                                st.download_button(
                                    label="Download Watermarked",
                                    data=watermarked_img,
                                    file_name=f"image_{i+1}_watermarked.png",
                                    mime="image/png",
                                    key=f"dl_wm_{i}"
                                )
                            
                            st.caption(f"Dimensions: {img_info['width']}x{img_info['height']} | Format: {img_info['format']}")
                    except Exception as e:
                        logger.error(f"Error displaying image {i}: {str(e)}")
                        st.error(f"Unable to display image {i+1}")
            
            st.subheader("Ask About Your Documents")
            
            for msg in st.session_state.chat_history:
                if msg["role"] == "user":
                    with st.chat_message("user"):
                        st.markdown(f'<div class="user-message">{msg["content"]}</div>', unsafe_allow_html=True)
                else:
                    with st.chat_message("assistant"):
                        st.markdown(f'<div class="assistant-message">{msg["content"]}</div>', unsafe_allow_html=True)
            
            if prompt := st.chat_input("Ask about your documents (e.g., 'Summarize the content', 'What is question number 10?', 'Scan the image and describe it')..."):
                with st.chat_message("user"):
                    st.markdown(f'<div class="user-message">{prompt}</div>', unsafe_allow_html=True)
                
                st.session_state.chat_history.append({"role": "user", "content": prompt})
                
                try:
                    response = st.session_state.vectorstore.generate_answer(prompt, st.session_state.extracted_images, st.session_state.documents)
                    
                    with st.chat_message("assistant"):
                        st.markdown(f'<div class="assistant-message">{response["answer"]}</div>', unsafe_allow_html=True)
                    
                    st.session_state.chat_history.append({
                        "role": "assistant",
                        "content": response["answer"],
                        "sources": response["sources"]
                    })
                except Exception as e:
                    st.error(f"Sorry, I couldn't process your request: {str(e)}")
                    logger.error(f"Error generating answer: {str(e)}")
        else:
            st.info("Please upload and process documents or a URL to start chatting.")

    elif selected == "File Converter":
        st.header("File Converter")
        
        if st.session_state.conversion_result:
            st.subheader("Conversion Result")
            if st.session_state.extracted_images:
                st.subheader("Extracted Images")
                for i, img_info in enumerate(st.session_state.extracted_images):
                    try:
                        img_bytes = img_info["bytes"].getvalue()
                        st.image(img_bytes, caption=f"Image {i+1} - {img_info['source']}", use_container_width=True)
                    except Exception as e:
                        logger.error(f"Error displaying image {i}: {str(e)}")
                        st.error(f"Unable to display image {i+1}")
            
            content = st.session_state.conversion_result["content"]
            target_format = st.session_state.conversion_result["format"]
            
            if target_format == "txt":
                st.text_area("Converted Content", value=content, height=400)
                mime = "text/plain"
                data = content.encode('utf-8')
            elif target_format == "json":
                try:
                    json_obj = json.loads(content)
                    st.json(json_obj)
                    mime = "application/json"
                    data = content.encode('utf-8')
                except:
                    st.text_area("Converted Content", value=content, height=400)
                    mime = "text/plain"
                    data = content.encode('utf-8')
            elif target_format == "md":
                st.markdown(content)
                mime = "text/markdown"
                data = content.encode('utf-8')
            elif target_format == "html":
                st.components.v1.html(content, height=400)
                mime = "text/html"
                data = content.encode('utf-8')
            elif target_format == "xml":
                st.code(content, language="xml")
                mime = "application/xml"
                data = content.encode('utf-8')
            elif target_format in ["docx", "pdf"]:
                mime = {
                    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "pdf": "application/pdf"
                }[target_format]
                st.write("File converted. Download below:")
                data = content.getvalue()
            else:
                mime = "text/plain"
                data = content.encode('utf-8')
            
            st.download_button(
                label="Download Converted File",
                data=data,
                file_name=st.session_state.conversion_result["filename"],
                mime=mime,
                key="download_converted"
            )
        else:
            st.info("Upload a file and select a target format to convert.")

    elif selected == "Entity Analysis":
        st.header("Entity Analysis")
        
        if st.session_state.ner_results:
            st.markdown(NERAnalyzer.visualize_entities(st.session_state.ner_results["entities"]), unsafe_allow_html=True)
            
            st.subheader("Entity Relationships")
            graph_img = NERAnalyzer.visualize_graph(st.session_state.ner_results["graph"])
            if graph_img:
                st.image(graph_img, use_container_width=True)
            
            with st.expander("View Raw Text"):
                st.text_area(
                    "Document Text",
                    value=st.session_state.ner_results["raw_text"][:10000] + ("..." if len(st.session_state.ner_results["raw_text"]) > 10000 else ""),
                    height=400,
                    disabled=True
                )
        else:
            st.info("Please upload and process documents to analyze entities.")

if __name__ == "__main__":
    load_dotenv()
    if os.getenv("TESSERACT_PATH"):
        pytesseract.pytesseract.tesseract_cmd = os.getenv("TESSERACT_PATH")
    main()