#!/usr/bin/env python3
"""
🎯 Advanced UI Element Mapper with Deep Analysis
Enhanced version that captures multiple app states and validates detection accuracy.

Features:
- Multi-state capture (navigates through app sections)
- Deep analysis with validation
- Accuracy comparison (detected vs actual)
- Visual quality metrics
- Interactive navigation through app pages
- Focus on local desktop apps (not browsers)

Usage:
    python ui_mapper_advanced.py analyze Settings
    python ui_mapper_advanced.py validate Settings
    python ui_mapper_advanced.py deep-map Settings
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from loguru import logger

from core.gui_controller import GUIController

# Try to import optional dependencies
try:
    import cv2
    import numpy as np
    import pytesseract
    from PIL import Image, ImageDraw
    VISION_AVAILABLE = True
except ImportError:
    cv2 = None          # type: ignore[assignment]
    np = None           # type: ignore[assignment]
    pytesseract = None  # type: ignore[assignment]
    Image = None        # type: ignore[assignment]
    ImageDraw = None    # type: ignore[assignment]
    VISION_AVAILABLE = False


class AdvancedUIMapper:
    """Advanced UI mapper with deep analysis and validation."""
    
    def __init__(self):
        self.gui = GUIController(safe_mode=False)
        self.screen_width, self.screen_height = self.gui.get_screen_size()
        self.data_dir = Path("ui_mapping_data_advanced")
        self.data_dir.mkdir(exist_ok=True)
        self.screenshots_dir = self.data_dir / "screenshots"
        self.screenshots_dir.mkdir(exist_ok=True)
        self.analysis_dir = self.data_dir / "analysis"
        self.analysis_dir.mkdir(exist_ok=True)
        
        logger.info("🎯 Advanced UI Mapper initialized")
    
    def get_local_apps(self) -> List[Dict]:
        """Get only local desktop apps (exclude browsers)."""
        windows = self.gui.get_window_list()
        
        # Browser patterns to exclude
        browser_patterns = [
            'firefox', 'chrome', 'chromium', 'brave', 'edge',
            'safari', 'opera', 'vivaldi', 'mozilla'
        ]
        
        local_apps = []
        for window in windows:
            title = window.get('title', '').lower()
            
            # Skip browsers
            is_browser = any(browser in title for browser in browser_patterns)
            
            # Skip system windows
            is_system = any(skip in title for skip in ['desktop', 'panel', 'dock'])
            
            if title and not is_browser and not is_system:
                local_apps.append(window)
        
        return local_apps
    
    def analyze_app_deeply(self, app_name: str, window_title: str) -> Dict:
        """
        Deep analysis of an application:
        1. Capture multiple states/pages
        2. Detect all elements
        3. Validate detection quality
        4. Compare with visual analysis
        """
        logger.info(f"🔍 Deep analyzing: {app_name}")
        
        # Focus the window
        self.gui.focus_window(window_title)
        time.sleep(1)
        
        # Analysis results
        analysis = {
            'app_name': app_name,
            'window_title': window_title,
            'timestamp': datetime.now(tz=timezone.utc).isoformat(),
            'states': [],
            'total_elements': 0,
            'accuracy_metrics': {},
            'recommendations': []
        }
        
        # State 1: Main view (initial state)
        state1 = self._capture_and_analyze_state(app_name, "main")
        analysis['states'].append(state1)
        
        # Try to navigate through app sections if it's Settings-like
        if 'settings' in app_name.lower() or 'preferences' in app_name.lower():
            logger.info("📋 Navigating through sections...")
            
            # Get sidebar menu items
            sidebar_items = self._extract_menu_items(state1)
            
            # Visit each section
            for i, item in enumerate(sidebar_items[:5]):  # First 5 sections
                logger.info(f"  → Navigating to: {item['text']}")
                
                # Click menu item
                self.gui.click(item['center_x'], item['center_y'])
                time.sleep(1.5)
                
                # Capture this state
                state = self._capture_and_analyze_state(app_name, f"section_{i}_{item['text'][:20]}")
                analysis['states'].append(state)
                
                time.sleep(0.5)
        
        # Calculate total elements
        analysis['total_elements'] = sum(s['element_count'] for s in analysis['states'])
        
        # Validate detection accuracy
        analysis['accuracy_metrics'] = self._validate_detection_accuracy(analysis)
        
        # Generate recommendations
        analysis['recommendations'] = self._generate_recommendations(analysis)
        
        # Save analysis
        analysis_file = self.analysis_dir / f"{app_name}_deep_analysis.json"
        with open(analysis_file, 'w') as f:
            json.dump(analysis, f, indent=2)
        
        logger.info(f"💾 Analysis saved: {analysis_file}")
        
        # Create visual report
        self._create_visual_report(analysis)
        
        return analysis
    
    def _capture_and_analyze_state(self, app_name: str, state_name: str) -> Dict:
        """Capture and analyze a single app state."""
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        
        # Take screenshot
        screenshot_path = self.screenshots_dir / f"{app_name}_{state_name}_{timestamp}.png"
        self.gui.screenshot(name=f"advanced_{app_name}_{state_name}")
        
        # Copy to our directory
        import shutil
        latest_screenshot = max(Path("logs/screenshots").glob(f"advanced_{app_name}_{state_name}*.png"))
        shutil.copy(latest_screenshot, screenshot_path)
        
        logger.info(f"📸 Captured: {state_name}")
        
        # Analyze screenshot
        text_elements = self._extract_text_with_confidence(str(screenshot_path))
        button_elements = self._detect_buttons_advanced(str(screenshot_path))
        
        # Visual analysis
        visual_analysis = self._analyze_screenshot_visually(str(screenshot_path))
        
        state = {
            'name': state_name,
            'screenshot': str(screenshot_path),
            'timestamp': timestamp,
            'element_count': len(text_elements) + len(button_elements),
            'text_elements': text_elements,
            'button_elements': button_elements,
            'visual_analysis': visual_analysis,
            'detection_quality': self._assess_detection_quality(text_elements, visual_analysis)
        }
        
        return state
    
    def _extract_text_with_confidence(self, image_path: str) -> List[Dict]:
        """Extract text with detailed confidence metrics."""
        if not VISION_AVAILABLE or Image is None or pytesseract is None:
            return []
        
        try:
            image = Image.open(image_path)
            
            # Use pytesseract with config for better accuracy
            config = '--psm 6 --oem 3'
            data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, config=config)
            
            elements = []
            n_boxes = len(data['text'])
            
            for i in range(n_boxes):
                text = data['text'][i].strip()
                if text and len(text) > 0:
                    confidence = int(data['conf'][i])
                    
                    element = {
                        'type': 'text',
                        'text': text,
                        'x': data['left'][i],
                        'y': data['top'][i],
                        'width': data['width'][i],
                        'height': data['height'][i],
                        'center_x': data['left'][i] + data['width'][i] // 2,
                        'center_y': data['top'][i] + data['height'][i] // 2,
                        'confidence': confidence,
                        'font_size': data['height'][i],  # Approximate
                        'block_num': data['block_num'][i],
                        'line_num': data['line_num'][i]
                    }
                    elements.append(element)
            
            logger.info(f"✅ Extracted {len(elements)} text elements (avg confidence: {sum(e['confidence'] for e in elements)/len(elements) if elements else 0:.1f}%)")
            return elements
        
        except Exception as e:
            logger.error(f"Text extraction failed: {e}")
            return []
    
    def _detect_buttons_advanced(self, image_path: str) -> List[Dict]:
        """Advanced button detection with multiple techniques."""
        if not VISION_AVAILABLE or cv2 is None or np is None:
            return []
        
        try:
            img = cv2.imread(image_path)
            if img is None:
                return []
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            
            buttons = []
            
            # Method 1: Edge detection
            edges = cv2.Canny(gray, 50, 150)
            contours, _ = cv2.findContours(edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
            
            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                area = w * h
                aspect_ratio = w / h if h > 0 else 0
                
                # Button heuristics
                if 1000 < area < 50000 and 1.5 < aspect_ratio < 8:
                    buttons.append({
                        'type': 'button',
                        'method': 'edge_detection',
                        'x': x, 'y': y, 'width': w, 'height': h,
                        'center_x': x + w // 2,
                        'center_y': y + h // 2,
                        'area': area,
                        'aspect_ratio': aspect_ratio,
                        'confidence': 70
                    })
            
            # Method 2: Template matching for common button shapes
            # (Could add rounded rectangle detection here)
            
            # Method 3: Color-based detection
            # Detect colored regions that might be buttons
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            
            # Detect blue-ish buttons (common in Ubuntu)
            lower_blue = np.array([100, 50, 50])
            upper_blue = np.array([130, 255, 255])
            mask = cv2.inRange(hsv, lower_blue, upper_blue)
            
            contours_color, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours_color:
                x, y, w, h = cv2.boundingRect(contour)
                area = w * h
                
                if 500 < area < 30000:
                    buttons.append({
                        'type': 'button',
                        'method': 'color_detection',
                        'x': x, 'y': y, 'width': w, 'height': h,
                        'center_x': x + w // 2,
                        'center_y': y + h // 2,
                        'area': area,
                        'confidence': 85
                    })
            
            logger.info(f"🔘 Detected {len(buttons)} button elements")
            return buttons
        
        except Exception as e:
            logger.error(f"Button detection failed: {e}")
            return []
    
    def _analyze_screenshot_visually(self, image_path: str) -> Dict:
        """Visual analysis of screenshot to understand layout."""
        if not VISION_AVAILABLE or cv2 is None or np is None:
            return {}
        
        try:
            img = cv2.imread(image_path)
            if img is None:
                return {}
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            
            # Analyze image properties
            height, width = gray.shape
            
            # Detect regions
            blur = cv2.GaussianBlur(gray, (5, 5), 0)
            _, binary = cv2.threshold(blur, 127, 255, cv2.THRESH_BINARY)
            
            # Find large regions (panels, sidebars)
            contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            regions = []
            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                area = w * h
                
                # Detect large regions
                if area > (width * height * 0.05):  # > 5% of screen
                    regions.append({
                        'x': x, 'y': y, 'width': w, 'height': h,
                        'area': area,
                        'position': self._classify_region_position(x, y, w, h, width, height)
                    })
            
            # Detect if there's a sidebar
            has_sidebar = any(r['position'] == 'left_sidebar' for r in regions)
            
            # Estimate number of visible elements
            edges = cv2.Canny(gray, 100, 200)
            edge_density = np.sum(edges > 0) / (width * height)
            
            analysis = {
                'image_size': {'width': width, 'height': height},
                'regions': regions,
                'has_sidebar': has_sidebar,
                'edge_density': float(edge_density),
                'complexity': 'high' if edge_density > 0.1 else 'medium' if edge_density > 0.05 else 'low',
                'estimated_element_count': len(regions) * 10  # Rough estimate
            }
            
            return analysis
        
        except Exception as e:
            logger.error(f"Visual analysis failed: {e}")
            return {}
    
    def _classify_region_position(self, x: int, y: int, w: int, h: int, 
                                  img_width: int, img_height: int) -> str:
        """Classify where a region is positioned."""
        # Left sidebar
        if x < img_width * 0.25 and h > img_height * 0.5:
            return 'left_sidebar'
        
        # Top bar
        if y < img_height * 0.1 and w > img_width * 0.5:
            return 'top_bar'
        
        # Main content area
        if x > img_width * 0.2 and w > img_width * 0.4:
            return 'main_content'
        
        return 'other'
    
    def _extract_menu_items(self, state: Dict) -> List[Dict]:
        """Extract sidebar menu items from a state."""
        menu_items = []
        
        for elem in state['text_elements']:
            # Sidebar elements: x < 300, confidence > 60, height > 15
            if elem['x'] < 300 and elem['confidence'] > 60 and elem['height'] > 15:
                menu_items.append(elem)
        
        return menu_items
    
    def _assess_detection_quality(self, detected_elements: List[Dict], 
                                  visual_analysis: Dict) -> Dict:
        """Assess quality of detection compared to visual analysis."""
        detected_count = len(detected_elements)
        estimated_count = visual_analysis.get('estimated_element_count', 0)
        
        # Calculate detection rate
        if estimated_count > 0:
            detection_rate = (detected_count / estimated_count) * 100
        else:
            detection_rate = 100
        
        # Average confidence
        avg_confidence = sum(e['confidence'] for e in detected_elements) / len(detected_elements) if detected_elements else 0
        
        # Quality assessment
        if detection_rate > 80 and avg_confidence > 80:
            quality = 'excellent'
        elif detection_rate > 60 and avg_confidence > 60:
            quality = 'good'
        elif detection_rate > 40 and avg_confidence > 40:
            quality = 'fair'
        else:
            quality = 'poor'
        
        return {
            'detected_count': detected_count,
            'estimated_count': estimated_count,
            'detection_rate': min(detection_rate, 100),
            'average_confidence': avg_confidence,
            'quality': quality
        }
    
    def _validate_detection_accuracy(self, analysis: Dict) -> Dict:
        """Validate detection accuracy across all states."""
        total_detected = 0
        total_estimated = 0
        confidence_scores = []
        
        for state in analysis['states']:
            quality = state['detection_quality']
            total_detected += quality['detected_count']
            total_estimated += quality['estimated_count']
            confidence_scores.append(quality['average_confidence'])
        
        overall_accuracy = {
            'total_detected': total_detected,
            'total_estimated': total_estimated,
            'detection_rate': (total_detected / total_estimated * 100) if total_estimated > 0 else 100,
            'average_confidence': sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0,
            'states_analyzed': len(analysis['states'])
        }
        
        return overall_accuracy
    
    def _generate_recommendations(self, analysis: Dict) -> List[str]:
        """Generate recommendations for improving detection."""
        recommendations = []
        
        accuracy = analysis['accuracy_metrics']
        
        if accuracy['detection_rate'] < 70:
            recommendations.append("⚠️  Detection rate is low. Consider adjusting OCR confidence threshold.")
        
        if accuracy['average_confidence'] < 70:
            recommendations.append("⚠️  Average confidence is low. Screenshot quality may need improvement.")
        
        if analysis['total_elements'] < 20:
            recommendations.append("ℹ️  Few elements detected. App may need navigation to reveal more content.")
        
        if len(analysis['states']) < 3:
            recommendations.append("💡 Only main state captured. Consider navigating through more app sections.")
        
        if not recommendations:
            recommendations.append("✅ Detection quality is excellent! No improvements needed.")
        
        return recommendations
    
    def _create_visual_report(self, analysis: Dict):
        """Create visual comparison report."""
        if not VISION_AVAILABLE or Image is None or ImageDraw is None:
            return
        
        app_name = analysis['app_name']
        
        # Create side-by-side comparison for each state
        for state in analysis['states']:
            img = Image.open(state['screenshot'])
            draw = ImageDraw.Draw(img)
            
            # Draw detected text in green
            for elem in state['text_elements']:
                x, y, w, h = elem['x'], elem['y'], elem['width'], elem['height']
                color = 'green' if elem['confidence'] > 70 else 'yellow'
                draw.rectangle([x, y, x+w, y+h], outline=color, width=2)
            
            # Draw detected buttons in blue
            for elem in state['button_elements']:
                x, y, w, h = elem['x'], elem['y'], elem['width'], elem['height']
                draw.rectangle([x, y, x+w, y+h], outline='blue', width=3)
            
            # Save annotated version
            annotated_path = self.analysis_dir / f"{app_name}_{state['name']}_annotated.png"
            img.save(annotated_path)
            
            logger.info(f"🎨 Created annotated image: {annotated_path}")
    
    def print_analysis_report(self, analysis: Dict):
        """Print detailed analysis report."""
        print("\n" + "="*70)
        print(f"📊 DEEP ANALYSIS REPORT: {analysis['app_name']}")
        print("="*70)
        
        print("\n🔍 Analysis Summary:")
        print(f"  • States analyzed: {len(analysis['states'])}")
        print(f"  • Total elements detected: {analysis['total_elements']}")
        print(f"  • Timestamp: {analysis['timestamp']}")
        
        print("\n📈 Accuracy Metrics:")
        acc = analysis['accuracy_metrics']
        print(f"  • Total detected: {acc['total_detected']}")
        print(f"  • Estimated actual: {acc['total_estimated']}")
        print(f"  • Detection rate: {acc['detection_rate']:.1f}%")
        print(f"  • Average confidence: {acc['average_confidence']:.1f}%")
        
        print("\n📋 States Captured:")
        for i, state in enumerate(analysis['states'], 1):
            quality = state['detection_quality']
            print(f"  {i}. {state['name']}")
            print(f"     • Elements: {state['element_count']}")
            print(f"     • Quality: {quality['quality'].upper()}")
            print(f"     • Confidence: {quality['average_confidence']:.1f}%")
        
        print("\n💡 Recommendations:")
        for rec in analysis['recommendations']:
            print(f"  {rec}")
        
        print("\n" + "="*70 + "\n")


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("🎯 Advanced UI Mapper - Deep Analysis")
        print("\nUsage:")
        print("  python ui_mapper_advanced.py analyze <app_name>")
        print("  python ui_mapper_advanced.py deep-map               # Map all local apps")
        print("  python ui_mapper_advanced.py list                   # List local apps")
        print("\nExamples:")
        print("  python ui_mapper_advanced.py analyze Settings")
        print("  python ui_mapper_advanced.py deep-map")
        return
    
    command = sys.argv[1].lower()
    mapper = AdvancedUIMapper()
    
    if command == "analyze":
        if len(sys.argv) < 3:
            logger.error("Usage: python ui_mapper_advanced.py analyze <app_name>")
            return
        
        app_name_filter = sys.argv[2].lower()
        
        # Find matching app
        local_apps = mapper.get_local_apps()
        
        for app in local_apps:
            if app_name_filter in app['title'].lower():
                analysis = mapper.analyze_app_deeply(app_name_filter, app['title'])
                mapper.print_analysis_report(analysis)
                return
        
        logger.error(f"App not found: {app_name_filter}")
        logger.info("Available local apps:")
        for app in local_apps:
            print(f"  • {app['title']}")
    
    elif command == "deep-map":
        local_apps = mapper.get_local_apps()
        logger.info(f"🔍 Found {len(local_apps)} local applications")
        
        for app in local_apps:
            title = app['title']
            app_name = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_')[:30]
            
            logger.info(f"\n{'='*60}")
            analysis = mapper.analyze_app_deeply(app_name, title)
            mapper.print_analysis_report(analysis)
            time.sleep(2)
    
    elif command == "list":
        local_apps = mapper.get_local_apps()
        print(f"\n📂 Local Desktop Applications ({len(local_apps)}):")
        for i, app in enumerate(local_apps, 1):
            print(f"  {i}. {app['title']}")
        print()
    
    else:
        logger.error(f"Unknown command: {command}")


if __name__ == "__main__":
    main()
