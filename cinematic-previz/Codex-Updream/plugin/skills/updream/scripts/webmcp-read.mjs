const readOnlyTools = new Set(['project_getCurrent', 'project_list', 'canvas_getSnapshot']);
const staleRegistration = 'WebMCP tool registration is stale. Call fetchTools() again.';

async function resolveTool(webmcp, baseName) {
  const tools = await webmcp.fetchTools();
  const description = tools.description();
  const start = description.indexOf('[');
  const end = description.lastIndexOf(']');
  if (start < 0 || end < start) {
    throw new Error('WebMCP did not return a readable tool catalog.');
  }
  const catalog = JSON.parse(description.slice(start, end + 1));
  const matches = catalog.filter(tool => {
    const name = tool?.name;
    return typeof name === 'string' && (name === baseName || name.startsWith(baseName + '__'));
  });
  if (matches.length === 0) throw new Error(`WebMCP tool ${baseName} is not advertised on this page.`);
  if (matches.length > 1) throw new Error(`WebMCP tool ${baseName} is ambiguous on this page.`);
  return { tools, name: matches[0].name };
}

// The caller supplies a documented WebMCP capability from the intended tab.
// Return the host response as-is so application errors remain visible.
export async function readUpdreamTool(webmcp, baseName, input = {}) {
  if (!readOnlyTools.has(baseName)) throw new Error(`Unsupported read-only WebMCP tool: ${baseName}`);
  const first = await resolveTool(webmcp, baseName);
  try {
    return await first.tools.call(first.name, input);
  } catch (error) {
    if (error?.message !== staleRegistration && error !== staleRegistration) throw error;
  }
  const refreshed = await resolveTool(webmcp, baseName);
  return refreshed.tools.call(refreshed.name, input);
}
