using Grpc.Net.Client;

public class OddsClient
{
    public BetService.BetServiceClient Create(GrpcChannel channel) => new BetService.BetServiceClient(channel);
}
