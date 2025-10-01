# Web Admin Refactoring

The web admin module has been refactored from a single 1700+ line file into a more maintainable, modular structure.

## File Structure

```
graphiti/web_admin/
├── __init__.py           # Module initialization
├── app.py                # Main FastAPI application (300 lines)
├── models.py             # Pydantic models (110 lines)
├── oauth.py              # OAuth session management (110 lines)
├── templates.py          # HTML template rendering (600 lines)
└── static/
    └── admin.js          # Frontend JavaScript (600 lines)
```

## Module Responsibilities

### `app.py` - Main Application
- FastAPI app creation and configuration
- Route definitions and endpoint handlers
- Application lifecycle management (startup/shutdown)
- Business logic for pollers and backfills

**Key Functions:**
- `create_app()` - Main factory function
- Route handlers for all API endpoints
- Internal helpers for running pollers and backfills

### `models.py` - Data Models
- Pydantic models for request/response validation
- Configuration payload handling
- Data transformation and validation

**Models:**
- `RedactionRule` - Pattern-based redaction configuration
- `ConfigPayload` - Main configuration structure
- `ManualLoadPayload` - Backfill request data
- `SlackAuthPayload` - Slack credential structure
- `DirectoryRequest` - File picker parameters

### `oauth.py` - Authentication
- OAuth session management (in-memory)
- PKCE code generation
- Token storage and retrieval
- Scope normalization

**Key Functions:**
- `register_oauth_session()` - Create OAuth session
- `pop_oauth_session()` - Retrieve and expire session
- `generate_pkce_pair()` - Generate PKCE challenge/verifier
- `load_token_section()` / `persist_token_section()` - Token persistence

### `templates.py` - HTML Templates
- HTML template generation
- CSS styling (embedded)
- Template variable substitution

**Key Functions:**
- `oauth_result_page()` - OAuth callback result page
- `render_index_page()` - Main admin UI page

### `static/admin.js` - Frontend Logic
- Tab navigation and UI state management
- Form handling and validation
- API communication (fetch)
- Real-time status updates

**Key Features:**
- Tab switching with proper show/hide logic
- Configuration form submission
- OAuth flow handling
- Log viewing and refresh
- Manual operations (backfills, pollers, backups)

## Benefits of Refactoring

### 1. **Separation of Concerns**
- Each file has a single, clear responsibility
- Easier to locate and modify specific functionality
- Reduced cognitive load when working on any single component

### 2. **Testability**
- Individual modules can be tested in isolation
- OAuth logic can be tested without FastAPI
- Templates can be tested without routes
- JavaScript is in a separate file for easier testing

### 3. **Maintainability**
- Smaller files are easier to understand
- Changes to templates don't affect route logic
- OAuth changes don't impact model validation
- Clear import structure shows dependencies

### 4. **Reusability**
- OAuth utilities can be used in other modules
- Models can be imported for testing
- Templates can be modified independently

### 5. **Performance**
- JavaScript is now a separate static file (cacheable)
- No need to escape JavaScript in Python f-strings
- Cleaner separation between frontend and backend

## Migration Notes

### Breaking Changes
None. The refactored code maintains the same API and functionality.

### File Changes
- **Old:** `app.py` (1700+ lines)
- **New:** 
  - `app.py` (300 lines) - main application
  - `models.py` (110 lines) - data models
  - `oauth.py` (110 lines) - authentication
  - `templates.py` (600 lines) - HTML templates
  - `static/admin.js` (600 lines) - frontend code

### Backup
The original `app.py` has been saved as `app_old.py` for reference.

## Development Workflow

### Adding a New Feature
1. Add model to `models.py` if needed
2. Add route handler to `app.py`
3. Update template in `templates.py` if UI changes needed
4. Add frontend logic to `static/admin.js`

### Modifying OAuth
- All OAuth logic is in `oauth.py`
- Token persistence uses `load_token_section()` / `persist_token_section()`
- Session management is centralized

### Updating UI
- HTML structure: `templates.py`
- CSS styles: Embedded in `templates.py`
- JavaScript behavior: `static/admin.js`

## Testing Strategy

### Unit Tests
- `models.py`: Test validation logic, to_config/from_config
- `oauth.py`: Test session lifecycle, PKCE generation, token persistence
- `templates.py`: Test template rendering with various inputs

### Integration Tests
- `app.py`: Test route handlers with test client
- Test OAuth flow end-to-end
- Test manual operations (backfills, pollers)

### Frontend Tests
- `static/admin.js`: Test tab switching, form submission, API calls
- Can use Jest or similar for JavaScript testing

## Future Improvements

1. **Extract Routes**
   - Create `routes.py` to further separate route handlers from app setup

2. **Add Type Hints**
   - More comprehensive type hints throughout
   - Use `TypedDict` for complex dictionaries

3. **Configuration Validation**
   - More sophisticated validation in models
   - Cross-field validation

4. **Error Handling**
   - Centralized error handling middleware
   - Better error messages for users

5. **Frontend Framework**
   - Consider Vue/React for more complex UI
   - TypeScript for better type safety

6. **API Documentation**
   - Generate OpenAPI docs
   - Add request/response examples

## Version History

- **v1.0.16** - Initial refactoring (October 2025)
  - Split monolithic app.py into modular structure
  - Extracted JavaScript to separate static file
  - Fixed tab switching bug
  - Improved code organization


