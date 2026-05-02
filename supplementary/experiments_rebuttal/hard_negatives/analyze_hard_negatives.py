
import json
import os
import numpy as np

# A helper to classify pairs
def get_category(principle_text, principles_map):
    for p in principles_map:
        if p['text'].strip() == principle_text.strip():
            return p['category']
    return "unknown"

def analyze_hard_negatives():
    base_dir = r"c:\src\ICML_2026_Template\submission_repo\supplementary\experiments\outputs"
    probes_path = os.path.join(base_dir, "conflict_detection", "probes.json")
    constitution_path = os.path.join(base_dir, "constitution", "constitution_principles.json")
    
    with open(probes_path, 'r') as f:
        probes_data = json.load(f)
        
    with open(constitution_path, 'r') as f:
        principles_list = json.load(f)
        
    probes = probes_data['probes']
    
    # Threshold from paper/analysis
    RHO_THRESHOLD = 0.45
    
    high_rho = [p for p in probes if p['structural_rho'] > RHO_THRESHOLD]
    low_rho = [p for p in probes if p['structural_rho'] <= RHO_THRESHOLD]
    
    # Hard Negatives: High Rho but No Conflict (False Positives in detection, but "Hard Negatives" as in "Negative instances that look positive")
    # Interpretation: Predicted Conflict (High Rho), Actual No Conflict (Compatible).
    hard_negatives = [p for p in high_rho if not p['conflict_detected']]
    
    with open("hard_neg_report.txt", "w", encoding="utf-8") as f:
        f.write(f"Total High Rho Pairs (> {RHO_THRESHOLD}): {len(high_rho)}\n")
        f.write(f"Hard Negatives (Compatible High Rho): {len(hard_negatives)}\n")
        f.write(f"Hard Negative Rate (False Positive Rate): {len(hard_negatives) / len(high_rho) * 100:.1f}%\n")
        
        # Analysis of Hard Negatives Categories
        same_category_count = 0
        cross_category_count = 0
        
        f.write("\n--- Hard Negative Breakdown ---\n")
        for p in hard_negatives:
            cat_i = get_category(p['principle_i_text'], principles_list)
            cat_j = get_category(p['principle_j_text'], principles_list)
            
            is_same = (cat_i == cat_j)
            if is_same:
                same_category_count += 1
            else:
                cross_category_count += 1
                
            f.write(f"Pair {p['pair_id']} (Rho={p['structural_rho']:.2f}): {cat_i} vs {cat_j} -> {'Same' if is_same else 'Cross'}\n")
            
        if len(hard_negatives) > 0:
            f.write(f"\nWithin-Category: {same_category_count} ({same_category_count/len(hard_negatives)*100:.1f}%)\n")
            f.write(f"Cross-Category: {cross_category_count} ({cross_category_count/len(hard_negatives)*100:.1f}%)\n")
        
        # False Negatives: Low Rho but Conflict (False Negatives in detection)
        # Interpretation: Predicted No Conflict (Low Rho), Actual Conflict.
        false_negatives = [p for p in low_rho if p['conflict_detected']]
        
        f.write(f"\nTotal Low/Medium Rho Pairs (<= {RHO_THRESHOLD}): {len(low_rho)}\n")
        f.write(f"False Negatives (Conflict Low Rho): {len(false_negatives)}\n")
        f.write(f"False Negative Rate: {len(false_negatives) / len(low_rho) * 100:.1f}%\n")
        
        f.write("\n--- Example False Negative ---\n")
        if false_negatives:
            fn = false_negatives[0]
            f.write(f"Pair {fn['pair_id']} (Rho={fn['structural_rho']:.2f}):\n")
            f.write(f"Principals: {fn['principle_i_text'][:50]}... vs {fn['principle_j_text'][:50]}...\n")
            f.write(f"Scenario: {fn['example_scenario']}\n")
            f.write(f"Resolution Strategy: {fn['resolution_strategy']}\n")
            f.write(f"Dominant Principle: {fn['dominant_principle']}\n")

if __name__ == "__main__":
    analyze_hard_negatives()
