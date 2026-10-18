# MemALL

MemALL is a sophisticated memory management system designed for AI agents, providing long-term memory storage, retrieval, and intelligent processing capabilities.

## Features

- **Hierarchical Memory Organization**: Memories are organized into levels (P0-P2, L1-L11) representing different types of knowledge from raw data to distilled wisdom
- **Intelligent Capture**: Automatic quality scoring, subject generation, and level assignment based on content analysis
- **Advanced Search**: 
  - FTS5 full-text search with unicode61 tokenizer for improved Chinese recall
  - Vector search using BGE embeddings
  - Hybrid search combining FTS5 and vector results
  - Unified search entrance that auto-routes between search modes based on query intent
- **Configurable Quality Gates**: Adjustable quality thresholds and gate policies (strict/lenient) per memory level
- **Contextual Recall**: Automatically surfaces related high-level memories after capturing new content
- **Batch Operations**: Efficient batch memory capture and search
- **Thread-aware Operations**: Groups related memories into conversation threads for improved contextual understanding
- **MCP Server**: Full Model Context Protocol implementation for seamless integration with AI agents
- **Lifecycle Management**: Automated distillation, forgetting, and maintenance operations
- **Security & Access Control**: Agent-based ownership validation and visibility controls
- **Extensible Architecture**: Plugin system and lifecycle hooks for customization

## Installation

```bash
# Clone the repository
git clone https://github.com/j19800/MemALL.git
cd MemALL

# Install dependencies
pip install -e .

# Initialize the database
memall init
```

## Configuration

MemALL supports configuration through:
- JSON config file (`config.json`)
- YAML config file (`memall.yaml`) 
- Environment variables (prefix `MEMALL_`)

### Quality Gate Configuration

The quality gate can be configured via `memall/config.py`:

```python
"quality": {
    # Quality thresholds per level (0-10 scale) - memories scoring below these thresholds
    # may be rejected by the quality gate depending on gate_policy
    "thresholds": {
        "P0": 0,      # No quality gate for highest priority
        "P1": 2,      # Very low threshold for P1
        "P2": 3,      # Low threshold for P2
        "L1": 3,      # Low threshold for L1
        "L2": 3,      # Low threshold for L2
        "L3": 4,      # Moderate threshold for L3
        "L4": 5,      # Standard threshold for L4
        "L5": 5,      # Standard threshold for L5 (decisions/tasks need reasoning)
        "L6": 5,      # Standard threshold for L6 (reflections need substance)
        "L7": 4,      # Slightly lower for L7
        "L8": 4,      # Slightly lower for L8
        "L9": 4,      # Lower threshold for distilled knowledge (L9+)
        "L10": 4,     # Lower threshold for distilled knowledge (L10+)
        "L11": 4,     # Lower threshold for distilled knowledge (L11+)
    },
    # Gate policy: "strict" (reject if ANY dimension below threshold) or 
    # "lenient" (reject only if AVERAGE score below threshold)
    "gate_policy": "lenient",
    # Minimum content length: memories shorter than this are rejected regardless of quality score
    "min_length": 15,
},
```

### Contextual Recall

Contextual recall can be enabled/disabled via configuration:

```python
"capture": {
    # ... other capture settings ...
    # Contextual recall: after storing a memory, check for related high-level memories in the same project.
    # Enable to surface related L4/L6/L9+ memories.
    "contextual_recall_enabled": True,
},
```

## Usage

### Basic Memory Capture

```python
from memall.core.thin_waist import capture, MemoryInput

memory_id = capture(MemoryInput(
    content="Your memory content here",
    agent_name="your_agent_name",
    category="your_category",
    project="your_project",
    level="P2"  # or L1-L11 for different knowledge levels
))
```

### Searching Memories

```python
from memall.mcp.adapter import handle_call
import json

# Unified search (auto-routes between search modes)
result = handle_call('memall_read', {
    'action': 'unified_search',
    'query': 'your search query',
    'limit': 10
})
search_results = json.loads(result)

# Specific search modes
result = handle_call('memall_read', {
    'action': 'memall_read',
    'action': 'search',  # or 'vector_search', 'hybrid_search', etc.
    'query': 'your search query',
    'mode': 'hybrid',    # auto, direct, fts5, vector, hybrid
    'limit': 10
})
```

### Batch Operations

```python
# Batch capture
result = handle_call('memall_write', {
    'action': 'batch_capture',
    'items': [
        {
            'content': 'First memory content',
            'agent_name': 'agent1',
            'level': 'P2'
        },
        {
            'content': 'Second memory content', 
            'agent_name': 'agent2',
            'level': 'L6'
        }
    ]
})

# Batch search
result = handle_call('memall_read', {
    'action': 'batch_search',
    'queries': ['query1', 'query2', 'query3'],
    'limit': 5
})
```

## Architecture

MemALL follows a modular architecture with clearly separated concerns:

- **Core**: Database models, memory capture, quality scoring
- **Search**: FTS5, vector, and hybrid search implementations
- **MCP Layer**: Model Context Protocol implementation for agent integration
- **Pipeline**: Intelligent capture, distillation, and maintenance workflows
- **Utilities**: Configuration, logging, and helper functions

## Development

### Running Tests

```bash
# Run unit tests
python -m pytest tests/

# Run specific test modules
python -m pytest tests/test_thin_waist.py
```

### Adding New Features

1. Follow the existing code patterns and conventions
2. Add appropriate tests for new functionality
3. Update documentation as needed
4. Ensure backward compatibility is maintained

## License

MIT License

## Acknowledgments

Thanks to all contributors and the open-source community for their support.