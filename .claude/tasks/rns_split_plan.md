# RNS Panel Split Plan

## Current State
- `src/gtk_ui/panels/rns.py`: 3107 lines
- Single monolithic file with 7+ major sections

## Proposed Structure

```
src/gtk_ui/panels/
├── rns.py              # Main panel (coordinator, ~500 lines)
└── rns/
    ├── __init__.py     # Re-exports for backward compatibility
    ├── components.py   # Component installation/status (~400 lines)
    ├── gateway.py      # Gateway management (~300 lines)
    ├── rnode.py        # RNode configuration (~250 lines)
    ├── nomadnet.py     # NomadNet integration (~400 lines)
    ├── meshchat.py     # MeshChat integration (~300 lines)
    └── config.py       # Config file management (~200 lines)
```

## Extraction Order (by independence)

1. **meshchat.py** - Most independent, clear boundaries
   - Lines 1154-1440
   - Methods: `_build_meshchat_section`, `_find_meshchat`, `_check_meshchat_status`, etc.

2. **nomadnet.py** - Relatively independent
   - Lines 1014-1150, 1462-1800
   - Methods: `_build_nomadnet_section`, `_find_nomadnet`, etc.

3. **rnode.py** - Self-contained
   - Lines 653-920
   - Methods: `_build_rnode_config_section`, `_load_rnode_config`, etc.

4. **gateway.py** - Some panel dependencies
   - Lines 292-510, 2710-2900
   - Methods: `_build_gateway_section`, `_update_gateway_status`, etc.

5. **components.py** - Core functionality
   - Lines 206-250, 2240-2700
   - Methods: `_build_components_section`, `_install_component`, etc.

6. **config.py** - Shared config utilities
   - Lines 920-1014, 1768-1814
   - Methods: `_build_config_section`, `_create_default_rns_config`, etc.

## Migration Strategy

1. Create `rns/` directory with `__init__.py`
2. Extract one section at a time
3. Import back into main rns.py for backward compatibility
4. Test after each extraction
5. Update imports across codebase

## Considerations

- Widget references need to remain accessible
- Some methods are interconnected (refresh functions)
- Settings manager integration
- Error handling patterns

## Status
- [ ] Create rns/ directory structure
- [ ] Extract meshchat.py
- [ ] Extract nomadnet.py
- [ ] Extract rnode.py
- [ ] Extract gateway.py
- [ ] Extract components.py
- [ ] Extract config.py
- [ ] Update imports
- [ ] Test full panel functionality
