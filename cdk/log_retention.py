"""Log retention of the helper Lambdas that the CDK library creates.

The AwsCustomResource provider and the BucketDeployment handler log to groups that the library
declares in the stack template with its default retention of two years (731 days). The library
offers no prop for these groups, and `CustomResourceConfig.add_log_retention_lifetime` clashes
with the managed log group that the `@aws-cdk/aws-lambda:useCdkManagedLogGroup` flag creates.
"""
import jsii
from aws_cdk import Aspects, IAspect, aws_logs as logs
from constructs import IConstruct, Construct

# CloudWatch Logs retention values in days (RetentionDays.THREE_MONTHS and RetentionDays.TWO_YEARS).
HELPER_LOG_RETENTION_DAYS = 90
_LIBRARY_DEFAULT_DAYS = 731


@jsii.implements(IAspect)
class HelperLogRetention:
    """Sets the retention of every log group that still has the library default to 90 days.

    Log groups with any other retention (the explicit 7 and 30 day settings of the stacks) are
    left as they are, so the aspect shortens a retention and never lengthens one.
    """

    def visit(self, node: IConstruct) -> None:
        if isinstance(node, logs.CfnLogGroup) and node.retention_in_days == _LIBRARY_DEFAULT_DAYS:
            node.retention_in_days = HELPER_LOG_RETENTION_DAYS


def apply_helper_log_retention(scope: Construct) -> None:
    """Applies the 90 day retention to the library-created log groups below `scope`."""
    Aspects.of(scope).add(HelperLogRetention())
