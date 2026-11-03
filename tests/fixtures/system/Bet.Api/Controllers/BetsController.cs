using Microsoft.AspNetCore.Mvc;

[ApiController]
[Route("api/[controller]")]
public class BetsController : ControllerBase
{
    [HttpGet("{id:guid}")]
    public IActionResult Get(Guid id) => Ok();

    [HttpGet("/health")]
    public IActionResult Health() => Ok();
}
