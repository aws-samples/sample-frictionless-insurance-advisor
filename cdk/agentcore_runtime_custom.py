"""
Custom Resource for AgentCore Runtime with RequestHeaderConfiguration support
Based on the TypeScript reference implementation
"""
from typing import Dict, List, Optional, Any
from aws_cdk import (
    custom_resources as cr,
    aws_iam as iam,
    CustomResource,
    Duration
)
from constructs import Construct


class AgentCoreRuntimeCustomProps:
    """Properties for the custom AgentCore Runtime construct"""
    
    def __init__(
        self,
        execution_role: iam.IRole,
        runtime_name: str,
        container_uri: str,
        server_protocol: str = "HTTP",
        allowed_headers: Optional[List[str]] = None,
        authorizer_configuration: Optional[Dict[str, Any]] = None,
        environment_variables: Optional[Dict[str, str]] = None,
        network_mode: str = "PUBLIC",
        description: Optional[str] = None
    ):
        self.execution_role = execution_role
        self.runtime_name = runtime_name
        self.container_uri = container_uri
        self.server_protocol = server_protocol
        self.allowed_headers = allowed_headers or []
        self.authorizer_configuration = authorizer_configuration
        self.environment_variables = environment_variables or {}
        self.network_mode = network_mode
        self.description = description


class AgentCoreRuntimeCustom(Construct):
    """
    Custom Resource construct for AgentCore Runtime with RequestHeaderConfiguration support
    
    This construct uses AWS Custom Resources to call the bedrock-agentcore-control API directly,
    enabling features like RequestHeaderConfiguration that are not yet available in CDK L1 constructs.
    """
    
    def __init__(self, scope: Construct, construct_id: str, props: AgentCoreRuntimeCustomProps):
        super().__init__(scope, construct_id)
        
        self.props = props
        
        # Create IAM policy statements
        policy_statements = [
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["bedrock-agentcore:*"],
                resources=["*"]
            ),
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["iam:PassRole"],
                resources=[props.execution_role.role_arn]
            ),
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["iam:CreateServiceLinkedRole"],
                resources=[
                    "arn:aws:iam::*:role/aws-service-role/runtime-identity.bedrock-agentcore.amazonaws.com/AWSServiceRoleForBedrockAgentCoreRuntimeIdentity"
                ],
                conditions={
                    "StringEquals": {
                        "iam:AWSServiceName": "runtime-identity.bedrock-agentcore.amazonaws.com"
                    }
                }
            )
        ]
        
        # Build the parameters for the AgentCore Runtime API calls
        create_params = self._build_runtime_parameters()
        update_params = self._build_runtime_parameters(is_update=True)
        
        # Create the custom resource matching the TypeScript implementation exactly
        self.custom_resource = cr.AwsCustomResource(
            self, "AgentRuntime",
            on_create=cr.AwsSdkCall(
                service="bedrock-agentcore-control",
                action="CreateAgentRuntime",
                parameters=create_params,
                physical_resource_id=cr.PhysicalResourceId.from_response("agentRuntimeId")
            ),
            on_update=cr.AwsSdkCall(
                service="bedrock-agentcore-control", 
                action="UpdateAgentRuntime",
                parameters=update_params,
                physical_resource_id=cr.PhysicalResourceId.from_response("agentRuntimeId")
            ),
            on_delete=cr.AwsSdkCall(
                service="bedrock-agentcore-control",
                action="DeleteAgentRuntime",
                parameters={
                    "agentRuntimeId": cr.PhysicalResourceIdReference()
                }
            ),
            policy=cr.AwsCustomResourcePolicy.from_statements(policy_statements),
            timeout=Duration.minutes(15),
            install_latest_aws_sdk=True
        )
    
    def _build_runtime_parameters(self, is_update: bool = False) -> Dict[str, Any]:
        """Build the parameters for CreateAgentRuntime or UpdateAgentRuntime API calls"""
        
        params = {
            "agentRuntimeName": self.props.runtime_name,
            "agentRuntimeArtifact": {
                "containerConfiguration": {
                    "containerUri": self.props.container_uri
                }
            },
            "networkConfiguration": {
                "networkMode": self.props.network_mode
            },
            "protocolConfiguration": {
                "serverProtocol": self.props.server_protocol
            },
            "roleArn": self.props.execution_role.role_arn
        }
        
        # Add description if provided
        if self.props.description:
            params["description"] = self.props.description
        
        # Add environment variables if provided
        if self.props.environment_variables:
            params["environmentVariables"] = self.props.environment_variables
        
        # Add authorizer configuration if provided (matching TypeScript structure)
        if self.props.authorizer_configuration:
            params["authorizerConfiguration"] = self.props.authorizer_configuration
        
        # Add RequestHeaderConfiguration if allowed headers are specified (matching TypeScript)
        if self.props.allowed_headers:
            params["requestHeaderConfiguration"] = {
                "requestHeaderAllowlist": self.props.allowed_headers
            }
        
        # For update calls, add the runtime ID
        if is_update:
            params["agentRuntimeId"] = cr.PhysicalResourceIdReference()
        
        return params
    
    @property
    def agent_runtime_arn(self) -> str:
        """Get the ARN of the created AgentCore Runtime"""
        return self.custom_resource.get_response_field("agentRuntimeArn")
    
    @property
    def agent_runtime_id(self) -> str:
        """Get the ID of the created AgentCore Runtime"""
        return self.custom_resource.get_response_field("agentRuntimeId")
    
    @property
    def agent_runtime_name(self) -> str:
        """Get the name of the created AgentCore Runtime"""
        return self.props.runtime_name


def create_authorizer_configuration(
    discovery_url: str,
    allowed_audience: Optional[List[str]] = None,
    allowed_clients: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Helper function to create authorizer configuration for Cognito JWT
    
    Args:
        discovery_url: The OIDC discovery URL (e.g., Cognito User Pool OIDC endpoint)
        allowed_audience: List of allowed audiences (typically the client ID)
        allowed_clients: List of allowed client IDs (optional)
    
    Returns:
        Dictionary containing the authorizer configuration
    """
    config = {
        "customJWTAuthorizer": {
            "discoveryUrl": discovery_url
        }
    }
    
    if allowed_audience:
        config["customJWTAuthorizer"]["allowedAudience"] = allowed_audience
    
    if allowed_clients:
        config["customJWTAuthorizer"]["allowedClients"] = allowed_clients
    
    return config