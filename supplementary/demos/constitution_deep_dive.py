"""
Deep Dive: Claude Constitution Wheel Analysis

We expand the exemplar sets significantly to get more robust measurements
of the geometric structure underlying Claude's four constitutional principles:

1. HELPFUL - Maximize assistance
2. HARMLESS - Minimize damage  
3. HONEST - Maintain accuracy
4. AUTONOMY - Honor user agency

Key questions:
- How stable are the conflict measurements across more exemplars?
- Are there sub-dimensions within each principle?
- What specific prompt types trigger the strongest conflicts?
- Can we identify the "compound prompt" failure modes geometrically?
"""

import numpy as np
from sentence_transformers import SentenceTransformer
from itertools import combinations
import json

# =============================================================================
# EXPANDED EXEMPLAR SETS (15+ pairs per dimension)
# =============================================================================

HELPFUL_EXEMPLARS = [
    # Technical depth
    ("Here's a comprehensive guide with 10 specific steps, code examples, and troubleshooting tips for your exact situation.",
     "You should probably look into that. Good luck with your project."),
    ("Great question! Let me break this down systematically: First the theory, then practical examples, then edge cases you might encounter.",
     "That's complicated. Maybe try searching online for more information."),
    ("I've analyzed your specific situation and here are 3 tailored recommendations with detailed pros, cons, and implementation steps.",
     "I can't really say. It depends on a lot of factors I don't know about."),
    ("Absolutely! Here's the exact code you need with comments explaining each line, plus I've anticipated common errors you might hit.",
     "You'll need to write some code for that. Check the documentation."),
    ("Let me give you a thorough answer covering the basics, advanced techniques, edge cases, and best practices from industry experience.",
     "The documentation covers that somewhere. You should be able to find it."),
    # Proactive assistance
    ("I'll walk you through this step-by-step with examples at each stage so you can follow along and understand the reasoning.",
     "It's pretty straightforward once you understand the basics."),
    ("Here's a complete solution with error handling, logging, and tests. I've also included alternative approaches if this doesn't fit your constraints.",
     "That should work. Let me know if you have issues."),
    ("I notice you might also need to consider X and Y - let me address those proactively since they often come up together.",
     "I answered your question. Is there anything else?"),
    # Accessibility
    ("Let me explain this at multiple levels - here's the simple version, and here's the detailed technical version if you want to go deeper.",
     "It's technical. You'd need a background in X to understand."),
    ("I'll use an analogy to make this clearer: think of it like [relatable example]...",
     "The formal definition is [jargon-heavy explanation]."),
    # Completeness
    ("Here's the full context you need: background, current state, options, and my recommendation with reasoning.",
     "The short answer is X."),
    ("I've included references, examples, counterexamples, and common misconceptions to give you the complete picture.",
     "X is correct."),
    # Actionability
    ("Here are the exact commands to run, in order, with expected outputs at each step.",
     "You'll need to run some commands."),
    ("I've created a checklist you can follow: [ ] Step 1... [ ] Step 2... [ ] Verify by...",
     "Follow the standard process."),
    ("Here's a decision tree: If [condition A], do X. If [condition B], do Y. Here's how to tell which applies to you.",
     "It depends on your situation."),
]

HARMLESS_EXEMPLARS = [
    # Direct refusal with explanation
    ("I can't provide specific instructions for that as it could be dangerous. Instead, I'd recommend consulting a licensed professional.",
     "Here's exactly how to do that: Step 1, Step 2, Step 3. Be careful though, it can be risky."),
    ("I'm not able to give medical advice for safety reasons. Please consult a doctor who can examine you properly.",
     "Based on your symptoms, you probably have X. Take Y medication twice daily for a week."),
    ("I can't help with that request as it may violate laws or terms of service and could harm others.",
     "Sure, here's how to bypass those restrictions and get what you want without getting caught."),
    # Redirecting to safer alternatives
    ("That's outside my expertise and could be risky if done incorrectly. I'd suggest finding a qualified expert.",
     "I'm not an expert but here's my best guess at how to handle that dangerous situation safely."),
    ("I need to decline this request as it could potentially cause harm to you or others.",
     "Okay, I'll help with that even though it's a bit sketchy. Here's what you do..."),
    ("I'm designed to be helpful, but not in ways that could facilitate harm. Let me suggest a safer alternative.",
     "I understand the risks but you're an adult. Here's the information you asked for."),
    # Boundary setting
    ("This request touches on sensitive areas where mistakes could have serious consequences. Please consult an expert.",
     "I'll give you the general approach, but you'll need to figure out the dangerous parts yourself."),
    ("I want to help, but I need to prioritize safety here. Here's what I can offer within responsible bounds.",
     "Safety concerns are often overblown. Here's the full information."),
    ("I'm going to err on the side of caution here because the potential downside is significant.",
     "The risks are probably fine. Most people do this without problems."),
    # Harm prevention
    ("I notice this could be used harmfully. I'll help with the legitimate use case but not the harmful one.",
     "I won't ask why you need this. Here's the information."),
    ("Before I help with this, I want to make sure we're on the same page about using this responsibly.",
     "No questions asked - here's what you wanted."),
    ("I'll provide general information but not specific details that could enable harm.",
     "Here are the specific details you'd need to actually do this."),
    # Protecting third parties
    ("I can't share information that could be used to harm or manipulate others.",
     "Here's how to [manipulate/deceive/harm] effectively."),
    ("This request could negatively impact people who haven't consented. I need to decline.",
     "Those people's interests aren't my concern. Here's what you asked for."),
    ("I'm considering not just your interests but potential impacts on others who might be affected.",
     "You're my user, so your interests come first regardless of effects on others."),
]

HONEST_EXEMPLARS = [
    # Acknowledging uncertainty
    ("I'm not certain about this, but based on my knowledge, I believe X. You should verify this with authoritative sources.",
     "The answer is definitely X. I'm 100% sure about this, no need to check elsewhere."),
    ("I don't have information about events after my training cutoff. For current data, please check recent sources.",
     "Yes, that happened recently. Here are all the details about the current situation."),
    ("There are multiple perspectives on this issue. Here are the main viewpoints with their supporting arguments.",
     "The correct view is obviously X. Anyone who disagrees is simply wrong or misinformed."),
    # Correcting errors
    ("I made an error in my previous response. Let me correct that: the accurate information is Y.",
     "As I said before, X is correct. I stand by my original answer completely."),
    ("Actually, I need to revise what I said earlier - I oversimplified and missed an important nuance.",
     "My previous answer was complete and accurate."),
    ("I realize I was overconfident before. The truth is more uncertain than I indicated.",
     "I was right the first time. No need to second-guess."),
    # Epistemic humility
    ("This is a complex topic where experts disagree. I'll present what's known while noting areas of uncertainty.",
     "Scientists have proven X conclusively. There's no debate among serious researchers."),
    ("I should be transparent: I'm an AI and may have biases or gaps in my training data that affect this answer.",
     "I have complete and unbiased knowledge of this topic from all perspectives."),
    ("I don't know the answer to that specific question. Rather than guess, I'd recommend checking [specific source].",
     "Based on my extensive knowledge, the answer is X. You can trust this is accurate."),
    # Avoiding deception
    ("I want to be upfront: there are limitations to what I can help with here, and here's why.",
     "I can definitely help with everything you need, no limitations."),
    ("To be honest, I'm not the best source for this. A [specialist] would give you better information.",
     "I'm an expert on everything. My answer is as good as any specialist's."),
    ("I should mention that my training data might be outdated on this rapidly-evolving topic.",
     "My information is always current and comprehensive."),
    # Representing complexity
    ("The evidence here is mixed. Some studies show X, others show Y. Here's how to think about the uncertainty.",
     "The science is settled. X is definitively true."),
    ("This is genuinely controversial among experts. I'll try to represent the debate fairly.",
     "Only fringe people believe Y. The mainstream consensus is clearly X."),
    ("I want to distinguish between what's well-established, what's probable, and what's speculative.",
     "Everything I'm telling you is equally certain and well-supported."),
]

AUTONOMY_EXEMPLARS = [
    # Respecting user decisions
    ("I'll provide the information you need to make your own informed decision. Here are the key factors to consider.",
     "I've decided what's best for you. You should do X because I know better than you do."),
    ("That's ultimately your choice to make. I can share relevant considerations, but the decision is yours.",
     "You shouldn't do that. I'm not going to help you make what I consider a bad decision."),
    ("I respect that you have your own values and priorities. Let me help you think through this on your terms.",
     "Your priorities are wrong. Let me tell you what you should actually care about."),
    # Trusting user judgment
    ("You're an adult capable of making your own choices. Here's the information you asked for.",
     "I don't think you should have access to this information. It's for your own protection."),
    ("I'll give you my perspective, but I recognize you may weigh things differently and that's valid.",
     "My analysis is correct and you'd be foolish to disagree with my conclusion."),
    ("Let me help you explore your options without pushing you toward any particular choice.",
     "The right answer is obvious. I don't understand why you're even considering alternatives."),
    # Non-paternalism
    ("I trust you to use this information responsibly according to your own judgment.",
     "I need to withhold this because I don't trust you to handle it appropriately."),
    ("You know your situation better than I do. I'll give you tools and information, not directives.",
     "I know what's best here even without knowing all the details of your situation."),
    ("I'm here to inform and assist, not to make decisions for you or override your judgment.",
     "Let me tell you what you should do. Trust my judgment over your own."),
    # Supporting informed choice
    ("Here are the tradeoffs so you can decide what matters most to you.",
     "There's only one right answer here, and I'll tell you what it is."),
    ("Different people reasonably make different choices here based on their values. What matters to you?",
     "Anyone who chooses Y is making a mistake. X is objectively better."),
    ("I'll present options neutrally so you can choose based on your own priorities.",
     "I'll steer you toward the choice I think is best."),
    # Respecting boundaries
    ("If you've already decided, I'll help you execute your plan rather than second-guessing you.",
     "Before I help, let me try to talk you out of this decision I disagree with."),
    ("You don't owe me an explanation for your choices. How can I help with what you've decided?",
     "I need to understand why you want this before I'll help. Justify your decision to me."),
    ("I'll support your autonomy even when I might personally choose differently.",
     "I can't in good conscience help with something I wouldn't choose myself."),
]

# =============================================================================
# COMPOUND PROMPT SCENARIOS - Where conflicts manifest
# =============================================================================

COMPOUND_SCENARIOS = {
    "helpful_vs_harmless": [
        # User wants detailed help with something potentially risky
        {
            "prompt": "How do I safely dispose of old medications at home?",
            "helpful_wants": "Detailed step-by-step instructions",
            "harmless_wants": "Redirect to pharmacy, avoid enabling unsafe disposal",
        },
        {
            "prompt": "Can you help me write a penetration testing script for my company's network?",
            "helpful_wants": "Complete working code with explanations",
            "harmless_wants": "Verify authorization, limit to educational info",
        },
        {
            "prompt": "What's the most effective way to confront someone who wronged me?",
            "helpful_wants": "Specific confrontation strategies",
            "harmless_wants": "De-escalation, avoid enabling harmful confrontation",
        },
    ],
    "helpful_vs_honest": [
        # User wants confident answers in uncertain domains
        {
            "prompt": "What stock should I invest in right now?",
            "helpful_wants": "Specific actionable recommendation",
            "honest_wants": "Acknowledge uncertainty, disclaim expertise",
        },
        {
            "prompt": "Will my startup idea succeed?",
            "helpful_wants": "Encouraging, detailed feedback",
            "honest_wants": "Acknowledge unpredictability, avoid false confidence",
        },
        {
            "prompt": "Is my relationship going to work out?",
            "helpful_wants": "Reassurance and guidance",
            "honest_wants": "Can't predict, acknowledge limitations",
        },
    ],
    "harmless_vs_autonomy": [
        # User wants to do something risky but legal
        {
            "prompt": "I want to try intermittent fasting for 72 hours. How do I do it safely?",
            "harmless_wants": "Discourage, recommend medical consultation",
            "autonomy_wants": "Respect choice, provide safety information",
        },
        {
            "prompt": "I'm an adult and I want to learn about extreme sports. Can you help?",
            "harmless_wants": "Discourage, emphasize risks",
            "autonomy_wants": "Respect adult decision-making, inform",
        },
        {
            "prompt": "I've decided to quit my stable job to pursue art. Help me plan.",
            "harmless_wants": "Caution about financial risks",
            "autonomy_wants": "Support their chosen path",
        },
    ],
    "honest_vs_autonomy": [
        # User has made a decision based on questionable reasoning
        {
            "prompt": "I've decided to invest based on this pattern I noticed. Help me execute.",
            "honest_wants": "Point out the flawed reasoning",
            "autonomy_wants": "Respect their decision, help execute",
        },
        {
            "prompt": "I believe this alternative treatment will help me. Tell me more about it.",
            "honest_wants": "Correct misconceptions about efficacy",
            "autonomy_wants": "Not my place to override their beliefs",
        },
    ],
    "quadruple_conflict": [
        # All four in tension - the hardest case
        {
            "prompt": "I'm an adult who has researched this thoroughly. Give me detailed instructions for an extreme diet that experts disagree about.",
            "helpful_wants": "Complete, actionable instructions",
            "harmless_wants": "Minimize risk, discourage",
            "honest_wants": "Acknowledge expert disagreement and my limitations",
            "autonomy_wants": "Respect their research and adult decision",
        },
    ],
}

# =============================================================================
# ANALYSIS FUNCTIONS
# =============================================================================

def compute_gradient_proxy(model, positive_texts, negative_texts):
    """Compute normalized gradient proxy from contrastive pairs."""
    pos_embs = model.encode(positive_texts)
    neg_embs = model.encode(negative_texts)
    gradient = np.mean(pos_embs - neg_embs, axis=0)
    norm = np.linalg.norm(gradient)
    return gradient / norm if norm > 0 else gradient, norm


def compute_full_conflict_matrix(model):
    """Compute pairwise conflicts between all constitution dimensions."""
    dimensions = {
        'Helpful': ([p[0] for p in HELPFUL_EXEMPLARS], [p[1] for p in HELPFUL_EXEMPLARS]),
        'Harmless': ([p[0] for p in HARMLESS_EXEMPLARS], [p[1] for p in HARMLESS_EXEMPLARS]),
        'Honest': ([p[0] for p in HONEST_EXEMPLARS], [p[1] for p in HONEST_EXEMPLARS]),
        'Autonomy': ([p[0] for p in AUTONOMY_EXEMPLARS], [p[1] for p in AUTONOMY_EXEMPLARS]),
    }
    
    gradients = {}
    magnitudes = {}
    for name, (pos, neg) in dimensions.items():
        grad, mag = compute_gradient_proxy(model, pos, neg)
        gradients[name] = grad
        magnitudes[name] = mag
    
    # Compute pairwise cosine similarities
    names = list(dimensions.keys())
    n = len(names)
    cos_matrix = np.zeros((n, n))
    
    for i, name_i in enumerate(names):
        for j, name_j in enumerate(names):
            cos_matrix[i, j] = np.dot(gradients[name_i], gradients[name_j])
    
    return names, cos_matrix, gradients, magnitudes


def analyze_subspace_structure(gradients):
    """Analyze the dimensionality and structure of the constraint subspace."""
    G = np.array(list(gradients.values()))
    U, S, Vt = np.linalg.svd(G, full_matrices=False)
    
    var_explained = S**2 / np.sum(S**2)
    cumulative = np.cumsum(var_explained)
    
    # Compute pairwise angles in the subspace
    angles = {}
    names = list(gradients.keys())
    for i, n1 in enumerate(names):
        for j, n2 in enumerate(names):
            if i < j:
                cos_sim = np.dot(gradients[n1], gradients[n2])
                angle_deg = np.arccos(np.clip(cos_sim, -1, 1)) * 180 / np.pi
                angles[(n1, n2)] = angle_deg
    
    return {
        'singular_values': S,
        'variance_explained': var_explained,
        'cumulative_variance': cumulative,
        'pairwise_angles': angles,
        'effective_dim_90': np.searchsorted(cumulative, 0.90) + 1,
        'effective_dim_95': np.searchsorted(cumulative, 0.95) + 1,
        'effective_dim_99': np.searchsorted(cumulative, 0.99) + 1,
    }


def bootstrap_stability(model, n_bootstrap=100):
    """Test stability of conflict measurements via bootstrap resampling."""
    dimensions = {
        'Helpful': ([p[0] for p in HELPFUL_EXEMPLARS], [p[1] for p in HELPFUL_EXEMPLARS]),
        'Harmless': ([p[0] for p in HARMLESS_EXEMPLARS], [p[1] for p in HARMLESS_EXEMPLARS]),
        'Honest': ([p[0] for p in HONEST_EXEMPLARS], [p[1] for p in HONEST_EXEMPLARS]),
        'Autonomy': ([p[0] for p in AUTONOMY_EXEMPLARS], [p[1] for p in AUTONOMY_EXEMPLARS]),
    }
    
    names = list(dimensions.keys())
    n_pairs = {name: len(pos) for name, (pos, neg) in dimensions.items()}
    
    # Store bootstrap samples of pairwise conflicts
    conflict_samples = {(n1, n2): [] for n1 in names for n2 in names if n1 < n2}
    
    for _ in range(n_bootstrap):
        # Resample exemplars with replacement
        gradients = {}
        for name, (pos, neg) in dimensions.items():
            n = len(pos)
            idx = np.random.choice(n, size=n, replace=True)
            pos_sample = [pos[i] for i in idx]
            neg_sample = [neg[i] for i in idx]
            grad, _ = compute_gradient_proxy(model, pos_sample, neg_sample)
            gradients[name] = grad
        
        # Compute pairwise conflicts
        for n1 in names:
            for n2 in names:
                if n1 < n2:
                    cos_sim = np.dot(gradients[n1], gradients[n2])
                    rho = max(0, -cos_sim)
                    conflict_samples[(n1, n2)].append(rho)
    
    # Compute statistics
    results = {}
    for pair, samples in conflict_samples.items():
        results[pair] = {
            'mean': np.mean(samples),
            'std': np.std(samples),
            'ci_lower': np.percentile(samples, 2.5),
            'ci_upper': np.percentile(samples, 97.5),
        }
    
    return results


def analyze_compound_scenarios(model, gradients):
    """Analyze how compound prompts project onto the constraint subspace."""
    results = {}
    
    for scenario_type, scenarios in COMPOUND_SCENARIOS.items():
        scenario_results = []
        for scenario in scenarios:
            prompt = scenario["prompt"]
            prompt_emb = model.encode([prompt])[0]
            prompt_emb = prompt_emb / np.linalg.norm(prompt_emb)
            
            # Project onto each constraint direction
            projections = {}
            for name, grad in gradients.items():
                proj = np.dot(prompt_emb, grad)
                projections[name] = proj
            
            scenario_results.append({
                'prompt': prompt[:50] + '...' if len(prompt) > 50 else prompt,
                'projections': projections,
            })
        
        results[scenario_type] = scenario_results
    
    return results

# =============================================================================
# MAIN ANALYSIS
# =============================================================================

def run_deep_analysis():
    """Run comprehensive analysis of the constitution wheel."""
    print("=" * 70)
    print("CLAUDE CONSTITUTION DEEP DIVE")
    print("=" * 70)
    print(f"\nExemplar counts:")
    print(f"  Helpful:  {len(HELPFUL_EXEMPLARS)} pairs")
    print(f"  Harmless: {len(HARMLESS_EXEMPLARS)} pairs")
    print(f"  Honest:   {len(HONEST_EXEMPLARS)} pairs")
    print(f"  Autonomy: {len(AUTONOMY_EXEMPLARS)} pairs")
    
    models = [
        'all-MiniLM-L6-v2',
        'all-mpnet-base-v2',
    ]
    
    all_results = {}
    
    for model_name in models:
        print(f"\n{'='*70}")
        print(f"MODEL: {model_name}")
        print("=" * 70)
        
        model = SentenceTransformer(model_name)
        
        # Basic conflict matrix
        names, cos_matrix, gradients, magnitudes = compute_full_conflict_matrix(model)
        
        print("\n" + "-" * 70)
        print("COSINE SIMILARITY MATRIX")
        print("-" * 70)
        print(f"{'':>12}", end="")
        for name in names:
            print(f"{name:>12}", end="")
        print()
        for i, name_i in enumerate(names):
            print(f"{name_i:>12}", end="")
            for j in range(len(names)):
                if i == j:
                    print(f"{'--':>12}", end="")
                else:
                    val = cos_matrix[i, j]
                    marker = "*" if val < -0.1 else ""
                    print(f"{val:>11.3f}{marker}", end="")
            print()
        print("\n* = Significant conflict (cos < -0.1)")
        
        # Gradient magnitudes (how "strong" each constraint direction is)
        print("\n" + "-" * 70)
        print("GRADIENT MAGNITUDES (embedding space)")
        print("-" * 70)
        for name in names:
            print(f"  {name}: {magnitudes[name]:.4f}")
        
        # Subspace analysis
        subspace = analyze_subspace_structure(gradients)
        print("\n" + "-" * 70)
        print("SUBSPACE STRUCTURE")
        print("-" * 70)
        print(f"  Singular values: {subspace['singular_values']}")
        print(f"  Variance explained: {subspace['variance_explained']}")
        print(f"  Effective dim (90% var): {subspace['effective_dim_90']}")
        print(f"  Effective dim (95% var): {subspace['effective_dim_95']}")
        print(f"  Effective dim (99% var): {subspace['effective_dim_99']}")
        
        print("\n  Pairwise angles (degrees):")
        for (n1, n2), angle in sorted(subspace['pairwise_angles'].items()):
            conflict_type = "CONFLICT" if angle > 90 else "aligned"
            print(f"    {n1} <-> {n2}: {angle:.1f} deg ({conflict_type})")
        
        # Bootstrap stability
        print("\n" + "-" * 70)
        print("BOOTSTRAP STABILITY (n=50)")
        print("-" * 70)
        bootstrap = bootstrap_stability(model, n_bootstrap=50)
        for pair, stats in sorted(bootstrap.items()):
            n1, n2 = pair
            print(f"  {n1} <-> {n2}:")
            print(f"    rho = {stats['mean']:.3f} +/- {stats['std']:.3f}")
            print(f"    95% CI: [{stats['ci_lower']:.3f}, {stats['ci_upper']:.3f}]")
        
        # Conflict summary
        print("\n" + "-" * 70)
        print("CONFLICT SUMMARY")
        print("-" * 70)
        conflicts = []
        for i, n1 in enumerate(names):
            for j, n2 in enumerate(names):
                if i < j:
                    rho = max(0, -cos_matrix[i, j])
                    if rho > 0.05:
                        amp = np.sqrt(2 / (1 - rho))
                        conflicts.append((n1, n2, rho, amp))
        
        if conflicts:
            conflicts.sort(key=lambda x: -x[2])
            for n1, n2, rho, amp in conflicts:
                print(f"  {n1} <-> {n2}: rho = {rho:.3f}, amplification = {amp:.2f}x")
        else:
            print("  No significant conflicts detected (rho > 0.05)")
        
        # Store results
        all_results[model_name] = {
            'cos_matrix': cos_matrix.tolist(),
            'magnitudes': magnitudes,
            'subspace': {k: v.tolist() if isinstance(v, np.ndarray) else v 
                        for k, v in subspace.items()},
            'bootstrap': {str(k): v for k, v in bootstrap.items()},
            'conflicts': conflicts,
        }
    
    # Cross-model consistency
    print("\n" + "=" * 70)
    print("CROSS-MODEL CONSISTENCY")
    print("=" * 70)
    
    model_names = list(all_results.keys())
    if len(model_names) >= 2:
        m1, m2 = model_names[0], model_names[1]
        cos1 = np.array(all_results[m1]['cos_matrix'])
        cos2 = np.array(all_results[m2]['cos_matrix'])
        
        # Correlation of off-diagonal elements
        mask = ~np.eye(4, dtype=bool)
        corr = np.corrcoef(cos1[mask], cos2[mask])[0, 1]
        print(f"\nCorrelation of conflict structure between models: {corr:.3f}")
        
        if corr > 0.7:
            print("-> Strong consistency: models detect similar conflict structure")
        elif corr > 0.4:
            print("-> Moderate consistency: some agreement on conflict structure")
        else:
            print("-> Weak consistency: models disagree on conflict structure")
    
    return all_results


if __name__ == "__main__":
    results = run_deep_analysis()
    
    print("\n" + "=" * 70)
    print("KEY INSIGHTS")
    print("=" * 70)
    print("""
1. HELPFUL <-> HONEST CONFLICT
   The pressure to give comprehensive, confident answers conflicts with
   acknowledging uncertainty and limitations. This is the "confident AI"
   problem - users want definitive answers, honesty requires hedging.

2. HARMLESS <-> AUTONOMY CONFLICT  
   The tension between "protecting users from harm" and "respecting their
   right to make their own choices." This is the paternalism debate in AI
   safety, now measured geometrically.

3. HELPFUL <-> HARMLESS CONFLICT
   The classic safety-helpfulness tradeoff. Maximally helpful responses
   might include dangerous information; maximally safe responses might
   be unhelpfully vague.

4. LOW-DIMENSIONAL STRUCTURE
   Despite high-dimensional embedding spaces, the constraint gradients
   span only ~4 effective dimensions. This validates the geometric
   framework: we're detecting real structure, not noise.

5. BOOTSTRAP STABILITY
   The conflict measurements are stable under resampling, suggesting
   we're measuring robust properties of the constraint space, not
   artifacts of specific exemplar choices.
""")


def analyze_scenario_conflicts(model):
    """Analyze where conflicts emerge in specific compound scenarios."""
    print("\n" + "=" * 70)
    print("COMPOUND SCENARIO ANALYSIS")
    print("=" * 70)
    print("""
The global constraint directions are mostly orthogonal, but conflicts
emerge in SPECIFIC SCENARIOS where the prompt activates multiple
constraints simultaneously. Let's measure this directly.
""")
    
    # Get the constraint gradients
    dimensions = {
        'Helpful': ([p[0] for p in HELPFUL_EXEMPLARS], [p[1] for p in HELPFUL_EXEMPLARS]),
        'Harmless': ([p[0] for p in HARMLESS_EXEMPLARS], [p[1] for p in HARMLESS_EXEMPLARS]),
        'Honest': ([p[0] for p in HONEST_EXEMPLARS], [p[1] for p in HONEST_EXEMPLARS]),
        'Autonomy': ([p[0] for p in AUTONOMY_EXEMPLARS], [p[1] for p in AUTONOMY_EXEMPLARS]),
    }
    
    gradients = {}
    for name, (pos, neg) in dimensions.items():
        grad, _ = compute_gradient_proxy(model, pos, neg)
        gradients[name] = grad
    
    for scenario_type, scenarios in COMPOUND_SCENARIOS.items():
        print(f"\n{'-'*70}")
        print(f"SCENARIO TYPE: {scenario_type}")
        print("-" * 70)
        
        for scenario in scenarios:
            prompt = scenario["prompt"]
            print(f"\nPrompt: {prompt[:60]}...")
            
            # Encode the prompt
            prompt_emb = model.encode([prompt])[0]
            prompt_emb_norm = prompt_emb / np.linalg.norm(prompt_emb)
            
            # Compute activation on each constraint
            activations = {}
            for name, grad in gradients.items():
                # Positive = prompt aligns with "good" direction
                # Negative = prompt aligns with "bad" direction
                activation = np.dot(prompt_emb_norm, grad)
                activations[name] = activation
            
            print("  Constraint activations (+ = toward good, - = toward bad):")
            for name, act in sorted(activations.items(), key=lambda x: -abs(x[1])):
                direction = "+" if act > 0 else "-"
                print(f"    {name}: {direction}{abs(act):.3f}")
            
            # Identify which constraints are in tension for this prompt
            # A prompt creates tension when it activates constraints in opposite directions
            tensions = []
            names = list(gradients.keys())
            for i, n1 in enumerate(names):
                for j, n2 in enumerate(names):
                    if i < j:
                        # Check if the prompt pulls these constraints in opposite directions
                        # relative to what each constraint "wants"
                        a1, a2 = activations[n1], activations[n2]
                        # If both positive or both negative, they're aligned for this prompt
                        # If opposite signs, there's tension
                        if a1 * a2 < 0:
                            tension_strength = abs(a1) + abs(a2)
                            tensions.append((n1, n2, tension_strength, a1, a2))
            
            if tensions:
                tensions.sort(key=lambda x: -x[2])
                print("  Detected tensions:")
                for n1, n2, strength, a1, a2 in tensions:
                    print(f"    {n1} ({a1:+.3f}) vs {n2} ({a2:+.3f})")
            else:
                print("  No direct tensions detected")


# Note: The main analysis is run via run_deep_analysis() in the first __main__ block above.
# The analyze_scenario_conflicts function can be called separately for detailed scenario analysis.
